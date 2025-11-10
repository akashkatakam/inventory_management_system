# inventory_manager.py

from typing import Any, Dict, List
from sqlalchemy.orm import Session
from sqlalchemy import func, case, and_, or_
import pandas as pd
from datetime import date
from inventory_models import InventoryTransaction, Branch, BranchHierarchy, TransactionType, VehiclePrice

# --- READ FUNCTIONS ---

def get_head_branches(db: Session):
    """Returns branches that are NOT sub-branches."""
    head_branch_ids = db.query(BranchHierarchy.Parent_Branch_ID)
    return db.query(Branch).filter(Branch.Branch_ID.in_(head_branch_ids)).all()

def get_managed_branches(db: Session, head_branch_id: str):
    """Returns all branches managed by this Head Branch."""
    sub_branches = db.query(Branch).join(
        BranchHierarchy, Branch.Branch_ID == BranchHierarchy.Sub_Branch_ID
    ).filter(BranchHierarchy.Parent_Branch_ID == head_branch_id).all()
    head_branch = db.query(Branch).filter(Branch.Branch_ID == head_branch_id).first()
    return [head_branch] + sub_branches if head_branch else sub_branches

def get_all_branches(db: Session):
    return db.query(Branch).order_by(Branch.Branch_ID).all()

def get_recent_transactions(db: Session, branch_id: str, limit: int = 50) -> pd.DataFrame:
    query = (
        db.query(InventoryTransaction)
        .filter(or_(
            InventoryTransaction.Current_Branch_ID == branch_id,
            InventoryTransaction.From_Branch_ID == branch_id,
            InventoryTransaction.To_Branch_ID == branch_id
        ))
        .order_by(InventoryTransaction.Date.desc(), InventoryTransaction.id.desc())
        .limit(limit)
    )
    return pd.read_sql(query.statement, db.get_bind())

def get_current_stock_summary(db: Session, branch_id: str) -> pd.DataFrame:
    net_quantity = case(
        (InventoryTransaction.Transaction_Type.in_([TransactionType.INWARD_OEM, TransactionType.INWARD_TRANSFER]), InventoryTransaction.Quantity),
        else_=-InventoryTransaction.Quantity
    )
    query = (
        db.query(
            InventoryTransaction.Model,
            InventoryTransaction.Variant,
            InventoryTransaction.Color,
            func.sum(net_quantity).label("Stock_On_Hand")
        )
        .filter(InventoryTransaction.Current_Branch_ID == branch_id)
        .group_by(InventoryTransaction.Model, InventoryTransaction.Variant, InventoryTransaction.Color)
        .having(func.sum(net_quantity) != 0)
    )
    return pd.read_sql(query.statement, db.get_bind())

def get_multi_branch_stock(db: Session, branch_ids: List[str]) -> pd.DataFrame:
    """
    Calculates combined current stock for a list of branches.
    Returns a DataFrame with Branch_Name included for pivoting if needed.
    """
    net_quantity = case(
        (InventoryTransaction.Transaction_Type.in_([TransactionType.INWARD_OEM, TransactionType.INWARD_TRANSFER]), InventoryTransaction.Quantity),
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
        .group_by(Branch.Branch_Name, InventoryTransaction.Model, InventoryTransaction.Variant, InventoryTransaction.Color)
        .having(func.sum(net_quantity) != 0)
    )
    
    return pd.read_sql(query.statement, db.get_bind())

# --- NEW: Vehicle Master Data ---
def get_vehicle_master_data(db: Session) -> dict:
    """
    Fetches all vehicles and structures them for cascading dropdowns.
    Returns: { Model_Name: { Variant_Name: [Color1, Color2, ...] } }
    """
    vehicles = db.query(VehiclePrice).all()
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

def log_oem_inward(db: Session, branch_id: str, model: str, var: str, color: str, qty: int, load_no: str, dt: date, rem: str):
    db.add(InventoryTransaction(
        Date=dt, Transaction_Type=TransactionType.INWARD_OEM,
        Current_Branch_ID=branch_id, Source_External="HMSI",
        Model=model, Variant=var, Color=color, Quantity=qty,
        Load_Number=load_no, Remarks=rem
    ))
    db.commit()

def log_transfer(db: Session, from_id: str, to_id: str, model: str, var: str, color: str, qty: int, dt: date, rem: str):
    try:
        txn_out = InventoryTransaction(
            Date=dt, Transaction_Type=TransactionType.OUTWARD_TRANSFER,
            Current_Branch_ID=from_id, To_Branch_ID=to_id,
            Model=model, Variant=var, Color=color, Quantity=qty,
            Remarks=f"Transfer OUT to {to_id} from {from_id}. {rem}"
        )
        txn_in = InventoryTransaction(
            Date=dt, Transaction_Type=TransactionType.INWARD_TRANSFER,
            Current_Branch_ID=to_id, From_Branch_ID=from_id,
            Model=model, Variant=var, Color=color, Quantity=qty,
            Remarks=f"Auto-transfer IN from {from_id} to {to_id}. {rem}"
        )
        db.add_all([txn_out, txn_in])
        db.commit()
    except Exception as e:
        db.rollback()
        raise e

def log_sale(db: Session, branch_id: str, model: str, var: str, color: str, qty: int, dt: date, rem: str):
    db.add(InventoryTransaction(
        Date=dt, Transaction_Type=TransactionType.SALE,
        Current_Branch_ID=branch_id,
        Model=model, Variant=var, Color=color, Quantity=qty,
        Remarks=rem
    ))
    db.commit()

def log_bulk_sales(db: Session, branch_id: str, date_val: date, remarks: str, vehicle_batch: List[Dict]):
    """
    Logs a batch of manual sales for a specific branch on a specific date.
    Applies the common branch_id, date, and remarks to every vehicle in the batch.
    """
    try:
        for item in vehicle_batch:
            db.add(InventoryTransaction(
                Date=date_val,
                Transaction_Type=TransactionType.SALE,
                Current_Branch_ID=branch_id, # Stock deducted from here
                Model=item['Model'], 
                Variant=item['Variant'], 
                Color=item['Color'], 
                Quantity=item['Quantity'],
                Remarks=remarks
            ))
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
def log_bulk_inward(db: Session, current_branch_id: str, source: str, load_no: str, date_val: date, remarks: str, vehicle_batch: List[Dict]):
    """Logs a batch of vehicles arriving. Handles mixed internal/external sources if needed."""
    try:
        # Check source type once for the whole batch
        is_internal = db.query(Branch).filter(Branch.Branch_ID == source).first()
        
        if is_internal:
            # If internal, every item is a transfer from 'source' to 'current_branch_id'
             for item in vehicle_batch:
                # 1. Outward from source
                db.add(InventoryTransaction(
                    Date=date_val, Transaction_Type=TransactionType.OUTWARD_TRANSFER,
                    Current_Branch_ID=source, To_Branch_ID=current_branch_id,
                    Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity'],
                    Remarks=f"Bulk Transfer OUT to {current_branch_id}. {remarks}"
                ))
                # 2. Inward to current
                db.add(InventoryTransaction(
                    Date=date_val, Transaction_Type=TransactionType.INWARD_TRANSFER,
                    Current_Branch_ID=current_branch_id, From_Branch_ID=source,
                    Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity'],
                    Remarks=f"Bulk Transfer IN from {source}. {remarks}", Load_Number=load_no
                ))
        else:
            # External OEM inward
            for item in vehicle_batch:
                db.add(InventoryTransaction(
                    Date=date_val, Transaction_Type=TransactionType.INWARD_OEM,
                    Current_Branch_ID=current_branch_id, Source_External=source,
                    Load_Number=load_no, Remarks=remarks,
                    Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity']
                ))
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    
def log_bulk_transfer(db: Session, from_branch_id: str, to_branch_id: str, date_val: date, remarks: str, vehicle_batch: List[Dict]):
    """Logs a batch transfer (Outward from sender, Inward to receiver)."""
    try:
        for item in vehicle_batch:
            # 1. Outward record
            db.add(InventoryTransaction(
                Date=date_val, Transaction_Type=TransactionType.OUTWARD_TRANSFER,
                Current_Branch_ID=from_branch_id, To_Branch_ID=to_branch_id,
                Remarks=f"Transfer OUT to {to_branch_id}. {remarks}",
                Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity']
            ))
            # 2. Inward record
            db.add(InventoryTransaction(
                Date=date_val, Transaction_Type=TransactionType.INWARD_TRANSFER,
                Current_Branch_ID=to_branch_id, From_Branch_ID=from_branch_id,
                Remarks=f"Transfer IN from {from_branch_id}. {remarks}",
                Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity']
            ))
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    
def log_inward_stock(db: Session, current_branch_id: str, source: str, 
                     model: str, variant: str, color: str, qty: int, 
                     load_no: str, date_val: date, remarks: str):
    """
    Logs new stock arriving at a branch.
    If source is another branch, it triggers a full transfer (deducting from source).
    If source is external (HMSI), it just adds stock.
    """
    # Check if source is an internal branch ID
    is_internal = db.query(Branch).filter(Branch.Branch_ID == source).first()
    
    if is_internal:
        # CRITICAL FIX: If it's from another branch, use the transfer logic to ensure double-entry
        # The 'source' is the 'from_id', and 'current_branch_id' is the 'to_id'
        log_transfer(db, source, current_branch_id, model, variant, color, qty, date_val, f"{remarks} (Logged via Inward)")
    else:
        # It's truly external (OEM), just add stock
        txn = InventoryTransaction(
            Date=date_val,
            Transaction_Type=TransactionType.INWARD_OEM,
            Current_Branch_ID=current_branch_id, 
            Source_External=source,
            Model=model.strip().upper(),
            Variant=variant.strip().upper(),
            Color=color.strip().upper(),
            Quantity=qty,
            Load_Number=load_no,
            Remarks=remarks
        )
        db.add(txn)
        db.commit()