# inventory_models.py

import hashlib
from sqlalchemy import Column, Float, Integer, String, Date, DateTime, ForeignKey, Enum, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
from database import Base

# Use a distinct Base if you want total isolation, 
# but sharing Base allows for easier relationship mapping if needed later.
# For now, we'll assume a standard Base for simplicity in this file.

# --- ENUMS ---
class TransactionType:
    INWARD_OEM = "HMSI"       # Stock arriving from manufacturer (+ Stock)
    INWARD_TRANSFER = "INWARD" # Stock arriving from another branch (+ Stock)
    OUTWARD_TRANSFER = "OUTWARD" # Stock leaving for another branch (- Stock)
    SALE = "Sale"

# --- SHARED TABLES (Read-Only views for Inventory App) ---
class Branch(Base):
    __tablename__ = "branches"
    Branch_ID = Column(String(10), primary_key=True)
    Branch_Name = Column(String(100))
    users = relationship("User", back_populates="branch")
    # No new columns added here!

# --- NEW: SIDECAR HIERARCHY TABLE ---
class BranchHierarchy(Base):
    """
    Stores parent-child relationships for inventory tracking.
    Only exists for the Inventory Management System.
    """
    __tablename__ = "branch_hierarchy"
    
    # Sub_Branch_ID is the Primary Key because a branch can only have ONE parent.
    Sub_Branch_ID = Column(String(10), ForeignKey("branches.Branch_ID"), primary_key=True)
    Parent_Branch_ID = Column(String(10), ForeignKey("branches.Branch_ID"), nullable=False)

    # Relationships for easy access
    sub_branch = relationship("Branch", foreign_keys=[Sub_Branch_ID])
    parent_branch = relationship("Branch", foreign_keys=[Parent_Branch_ID])


# --- INVENTORY TRANSACTION TABLE ---
class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"

    id = Column(Integer, primary_key=True, index=True)
    Timestamp = Column(DateTime, default=datetime.utcnow)
    Date = Column(Date, nullable=False)
    Transaction_Type = Column(String(20), nullable=False) 
    Source_External = Column(String(50), nullable=True)
    From_Branch_ID = Column(String(10), ForeignKey("branches.Branch_ID"), nullable=True)
    Current_Branch_ID = Column(String(10), ForeignKey("branches.Branch_ID"), nullable=False)
    To_Branch_ID = Column(String(10), ForeignKey("branches.Branch_ID"), nullable=True)
    Model = Column(String(100), nullable=False)
    Variant = Column(String(100), nullable=False)
    Color = Column(String(50), nullable=False)
    Quantity = Column(Integer, nullable=False, default=1)
    Load_Number = Column(String(50))
    Remarks = Column(String(255))

class VehiclePrice(Base):
    """
    Stores the full list of pricing components from the CSV.
    NOTE: Column names match your headers exactly for simple ingestion.
    """
    __tablename__ = "vehicle_prices"
    
    id = Column(Integer, primary_key=True, index=True)
    Model = Column(String(100), index=True)
    Variant = Column(String(100))
    
    # --- FULL PRICING COLUMNS ---
    EX_SHOWROOM = Column(Float)
    LIFE_TAX = Column(Float)
    INSURANCE_1_4 = Column(Float)
    ORP = Column(Float)
    ACCESSORIES = Column(Float)   # NEW
    EW_3_1 = Column(Float)        # Renamed to Python-friendly format
    HC = Column(Float)            # NEW
    PR_CHARGES = Column(Float)    # NEW
    FINAL_PRICE = Column(Float) 
    # --- END FULL PRICING COLUMNS ---
    
    Color_List = Column(String(500)) 

class User(Base):
    """Stores user logins and roles for the dashboard."""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    
    # We will store the salt and the hash
    hashed_password = Column(String(255), nullable=False)
    salt = Column(String(64), nullable=False) # Store the salt
    
    role = Column(Enum("Owner", "Back Office","PDI"), nullable=False)

    Branch_ID = Column(String(10), ForeignKey("branches.Branch_ID"), nullable=True)
    
    # --- NEW RELATIONSHIP ---
    branch = relationship("Branch", back_populates="users")
    
    def verify_password(self, plain_password: str) -> bool:
        """Checks if the plain password matches the hash."""
        
        # --- CRITICAL FIX ---
        # 1. Convert the stored hex salt back into raw bytes
        salt_bytes = bytes.fromhex(self.salt)
        
        # 2. Hash the provided password with the retrieved salt
        check_hash_bytes = hashlib.pbkdf2_hmac(
            'sha256',
            plain_password.encode('utf-8'),
            salt_bytes, # Use the raw bytes
            100000
        )
        
        # 3. Compare the new hash (in hex) with the stored hash (in hex)
        return check_hash_bytes.hex() == self.hashed_password
        # --- END FIX ---