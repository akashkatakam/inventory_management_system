# inventory_manager.py

from sqlalchemy.orm import Session, aliased
from sqlalchemy import func, case, or_
import pandas as pd
from datetime import date, datetime
from inventory_models import InventoryTransaction, Branch, BranchHierarchy, TransactionType, VehiclePrice
from typing import Dict, Any, List, Optional
import streamlit as st

# --- READ FUNCTIONS ---

def get_head_branches(db: Session):
    """Returns branches that are NOT sub-branches (i.e., Head Offices)."""
    sub_branch_ids = db.query(BranchHierarchy.Sub_Branch_ID)
    return db.query(Branch).filter(Branch.Branch_ID.not_in(sub_branch_ids)).all()

def get_managed_branches(db: Session, head_branch_id: str):
    """Returns all branches managed by this Head Branch (Head + Subs)."""
    sub_branches = db.query(Branch).join(
        BranchHierarchy, Branch.Branch_ID == BranchHierarchy.Sub_Branch_ID
    ).filter(BranchHierarchy.Parent_Branch_ID == head_branch_id).all()
    head_branch = db.query(Branch).filter(Branch.Branch_ID == head_branch_id).first()
    return [head_branch] + sub_branches if head_branch else sub_branches

def get_all_branches(db: Session):
    return db.query(Branch).order_by(Branch.Branch_ID).all()

def get_recent_transactions(db: Session, branch_ids: List[str], start_date: Optional[date] = None, end_date: Optional[date] = None, limit: int = 100) -> pd.DataFrame:
    """Fetches recent transactions for a LIST of branches, with optional date filtering."""
    if not isinstance(branch_ids, list):
        branch_ids = [branch_ids]
        
    query = db.query(InventoryTransaction).filter(or_(
        InventoryTransaction.Current_Branch_ID.in_(branch_ids),
        InventoryTransaction.From_Branch_ID.in_(branch_ids),
        InventoryTransaction.To_Branch_ID.in_(branch_ids)
    ))

    if start_date and end_date:
        query = query.filter(InventoryTransaction.Date >= start_date, InventoryTransaction.Date <= end_date)
    else:
        query = query.limit(limit)

    query = query.order_by(InventoryTransaction.Date.desc(), InventoryTransaction.id.desc())
    return pd.read_sql(query.statement, db.get_bind())

def get_multi_branch_stock(db: Session, branch_ids: List[str], as_of_date: Optional[date] = None) -> pd.DataFrame:
    """Calculates combined stock for a list of branches."""
    net_quantity = case(
        (InventoryTransaction.Transaction_Type.in_([TransactionType.INWARD_OEM, TransactionType.INWARD_TRANSFER]), InventoryTransaction.Quantity),
        (InventoryTransaction.Transaction_Type == TransactionType.ADJUSTMENT, InventoryTransaction.Quantity), 
        else_=-InventoryTransaction.Quantity
    )
    
    query = (
        db.query(
            Branch.Branch_Name,
            InventoryTransaction.Model,
            InventoryTransaction.Variant,
            InventoryTransaction.Color,
            func.sum(net_quantity).label("Stock")
        )
        .join(Branch, InventoryTransaction.Current_Branch_ID == Branch.Branch_ID)
        .filter(InventoryTransaction.Current_Branch_ID.in_(branch_ids))
    )
    
    if as_of_date:
        query = query.filter(InventoryTransaction.Date <= as_of_date)

    query = (
        query.group_by(Branch.Branch_Name, InventoryTransaction.Model, InventoryTransaction.Variant, InventoryTransaction.Color)
        .having(func.sum(net_quantity) != 0)
    )
    
    return pd.read_sql(query.statement, db.get_bind())

def get_stock_for_single_item(db: Session, branch_id: str, model: str, variant: str, color: str) -> int:
    """Calculates the current stock for one specific vehicle."""
    net_quantity = case(
        (InventoryTransaction.Transaction_Type.in_([TransactionType.INWARD_OEM, TransactionType.INWARD_TRANSFER]), InventoryTransaction.Quantity),
        (InventoryTransaction.Transaction_Type == TransactionType.ADJUSTMENT, InventoryTransaction.Quantity),
        else_=-InventoryTransaction.Quantity
    )
    
    stock = db.query(func.sum(net_quantity)).filter(
        InventoryTransaction.Current_Branch_ID == branch_id,
        InventoryTransaction.Model == model,
        InventoryTransaction.Variant == variant,
        InventoryTransaction.Color == color
    ).scalar()
    
    return int(stock) if stock else 0

@st.cache_data(ttl=3600)
def get_vehicle_master_data(_db: Session) -> dict:
    """Fetches all vehicles and structures them for cascading dropdowns."""
    vehicles = _db.query(VehiclePrice).all()
    master_data = {}
    for v in vehicles:
        if v.Model not in master_data:
            master_data[v.Model] = {}
        if v.Color_List:
             colors = sorted([c.strip() for c in v.Color_List.split(',') if c.strip()])
        else:
             colors = ["N/A"]
        master_data[v.Model][v.Variant] = colors
    return master_data

# --- WRITE FUNCTIONS ---

def log_bulk_inward(db: Session, current_branch_id: str, source: str, load_no: str, date_val: date, remarks: str, vehicle_batch: List[Dict]):
    """Logs a batch of vehicles arriving. Handles internal/external sources."""
    try:
        is_internal = db.query(Branch).filter(Branch.Branch_ID == source).first()
        
        for item in vehicle_batch:
            if is_internal:
                 # Internal Transfer
                 db.add(InventoryTransaction(Date=date_val, Transaction_Type=TransactionType.OUTWARD_TRANSFER, Current_Branch_ID=source, To_Branch_ID=current_branch_id, Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity'], Remarks=f"Bulk Transfer OUT. {remarks}"))
                 db.add(InventoryTransaction(Date=date_val, Transaction_Type=TransactionType.INWARD_TRANSFER, Current_Branch_ID=current_branch_id, From_Branch_ID=source, Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity'], Remarks=f"Bulk Transfer IN. {remarks}", Load_Number=load_no))
            else:
                 # External OEM Inward
                 db.add(InventoryTransaction(Date=date_val, Transaction_Type=TransactionType.INWARD_OEM, Current_Branch_ID=current_branch_id, Source_External=source, Load_Number=load_no, Remarks=remarks, Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity']))
        db.commit()
    except Exception as e:
        db.rollback()
        raise e

def log_bulk_transfer(db: Session, from_branch_id: str, to_branch_id: str, date_val: date, remarks: str, vehicle_batch: List[Dict]):
    """Logs a batch transfer (Outward from sender, Inward to receiver)."""
    try:
        for item in vehicle_batch:
            db.add(InventoryTransaction(Date=date_val, Transaction_Type=TransactionType.OUTWARD_TRANSFER, Current_Branch_ID=from_branch_id, To_Branch_ID=to_branch_id, Remarks=f"Transfer OUT. {remarks}", Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity']))
            db.add(InventoryTransaction(Date=date_val, Transaction_Type=TransactionType.INWARD_TRANSFER, Current_Branch_ID=to_branch_id, From_Branch_ID=from_branch_id, Remarks=f"Transfer IN. {remarks}", Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity']))
        db.commit()
    except Exception as e: db.rollback(); raise e

def log_bulk_sales(db: Session, branch_id: str, date_val: date, remarks: str, vehicle_batch: List[Dict]):
    """Logs a batch of manual sales."""
    try:
        for item in vehicle_batch:
            db.add(InventoryTransaction(Date=date_val, Transaction_Type=TransactionType.SALE, Current_Branch_ID=branch_id, Remarks=remarks, Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity']))
        db.commit()
    except Exception as e: db.rollback(); raise e

def log_stock_adjustment(db: Session, branch_id: str, model: str, variant: str, color: str, 
                         adjustment_qty: int, date_val: date, remarks: str, user: str):
    """Logs a positive or negative adjustment to the inventory."""
    if adjustment_qty == 0:
        return # Do nothing
    txn = InventoryTransaction(
        Date=date_val,
        Transaction_Type=TransactionType.ADJUSTMENT,
        Current_Branch_ID=branch_id,
        Model=model.strip().upper(),
        Variant=variant.strip().upper(),
        Color=color.strip().upper(),
        Quantity=adjustment_qty, # This is the change (+5 or -2)
        Remarks=f"ADJUSTMENT by {user}: {remarks}"
    )
    db.add(txn)
    db.commit()