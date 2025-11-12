# inventory_manager.py

from typing import Any, Dict, List
from sqlalchemy.orm import Session, aliased
from sqlalchemy import func, case, and_, or_
import pandas as pd
from datetime import date, datetime
# --- UPDATED: Import all models from the new merged file ---
import inventory_models as models
from inventory_models import IST_TIMEZONE, TransactionType

# --- READ FUNCTIONS (Existing) ---

def get_head_branches(db: Session):
    """Returns branches that are NOT sub-branches."""
    head_branch_ids = db.query(models.BranchHierarchy.Parent_Branch_ID)
    return db.query(models.Branch).filter(models.Branch.Branch_ID.in_(head_branch_ids)).all()

def get_managed_branches(db: Session, head_branch_id: str):
    """Returns all branches managed by this Head Branch."""
    sub_branches = db.query(models.Branch).join(
        models.BranchHierarchy, models.Branch.Branch_ID == models.BranchHierarchy.Sub_Branch_ID
    ).filter(models.BranchHierarchy.Parent_Branch_ID == head_branch_id).all()
    head_branch = db.query(models.Branch).filter(models.Branch.Branch_ID == head_branch_id).first()
    return [head_branch] + sub_branches if head_branch else sub_branches

def get_all_branches(db: Session):
    return db.query(models.Branch).order_by(models.Branch.Branch_ID).all()

def get_recent_transactions(db: Session, branch_id: str, limit: int = 50) -> pd.DataFrame:
    query = (
        db.query(models.InventoryTransaction)
        .filter(or_(
            models.InventoryTransaction.Current_Branch_ID == branch_id,
            models.InventoryTransaction.From_Branch_ID == branch_id,
            models.InventoryTransaction.To_Branch_ID == branch_id
        ))
        .order_by(models.InventoryTransaction.Date.desc(), models.InventoryTransaction.id.desc())
        .limit(limit)
    )
    return pd.read_sql(query.statement, db.get_bind())

def get_current_stock_summary(db: Session, branch_id: str) -> pd.DataFrame:
    net_quantity = case(
        (models.InventoryTransaction.Transaction_Type.in_([TransactionType.INWARD_OEM, TransactionType.INWARD_TRANSFER]), models.InventoryTransaction.Quantity),
        else_=-models.InventoryTransaction.Quantity
    )
    query = (
        db.query(
            models.InventoryTransaction.Model,
            models.InventoryTransaction.Variant,
            models.InventoryTransaction.Color,
            func.sum(net_quantity).label("Stock_On_Hand")
        )
        .filter(models.InventoryTransaction.Current_Branch_ID == branch_id)
        .group_by(models.InventoryTransaction.Model, models.InventoryTransaction.Variant, models.InventoryTransaction.Color)
        .having(func.sum(net_quantity) != 0)
    )
    return pd.read_sql(query.statement, db.get_bind())

def get_multi_branch_stock(db: Session, branch_ids: List[str]) -> pd.DataFrame:
    """
    Calculates combined current stock for a list of branches.
    Returns a DataFrame with Branch_Name included for pivoting if needed.
    """
    net_quantity = case(
        (models.InventoryTransaction.Transaction_Type.in_([TransactionType.INWARD_OEM, TransactionType.INWARD_TRANSFER]), models.InventoryTransaction.Quantity),
        else_=-models.InventoryTransaction.Quantity
    )
    
    query = (
        db.query(
            models.Branch.Branch_Name,
            models.InventoryTransaction.Model,
            models.InventoryTransaction.Variant,
            models.InventoryTransaction.Color,
            func.sum(net_quantity).label("Stock")
        )
        .join(models.Branch, models.InventoryTransaction.Current_Branch_ID == models.Branch.Branch_ID)
        .filter(models.InventoryTransaction.Current_Branch_ID.in_(branch_ids))
        .group_by(models.Branch.Branch_Name, models.InventoryTransaction.Model, models.InventoryTransaction.Variant, models.InventoryTransaction.Color)
        .having(func.sum(net_quantity) != 0)
    )
    
    return pd.read_sql(query.statement, db.get_bind())

def get_daily_transfer_summary(db: Session, limit: int = 100) -> pd.DataFrame:
    """
    Returns a day-by-day summary of ALL vehicle transfers between branches.
    Designed for Owner/PDI high-level review.
    """
    # Aliases for joining the Branch table twice (once for sender, once for receiver)
    FromBranch = aliased(models.Branch)
    ToBranch = aliased(models.Branch)

    query = (
        db.query(
            models.InventoryTransaction.Date,
            FromBranch.Branch_Name.label("From_Branch"),
            ToBranch.Branch_Name.label("To_Branch"),
            func.sum(models.InventoryTransaction.Quantity).label("Total_Qty"),
        )
        .join(FromBranch, models.InventoryTransaction.From_Branch_ID == FromBranch.Branch_ID)
        .join(ToBranch, models.InventoryTransaction.To_Branch_ID == ToBranch.Branch_ID)
        .filter(models.InventoryTransaction.Transaction_Type == TransactionType.OUTWARD_TRANSFER)
        .group_by(
            models.InventoryTransaction.Date,
            FromBranch.Branch_Name,
            ToBranch.Branch_Name
        )
        .order_by(models.InventoryTransaction.Date.desc())
        .limit(limit)
    )
    
    return pd.read_sql(query.statement, db.get_bind())

# --- NEW: Vehicle Master Data ---
def get_vehicle_master_data(db: Session) -> dict:
    """
    Fetches all vehicles and structures them for cascading dropdowns.
    Returns: { Model_Name: { Variant_Name: [Color1, Color2, ...] } }
    """
    vehicles = db.query(models.VehiclePrice).all()
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

# --- WRITE FUNCTIONS (Existing) ---

def log_oem_inward(db: Session, branch_id: str, model: str, var: str, color: str, qty: int, load_no: str, dt: date, rem: str):
    db.add(models.InventoryTransaction(
        Date=dt, Transaction_Type=TransactionType.INWARD_OEM,
        Current_Branch_ID=branch_id, Source_External="HMSI",
        Model=model, Variant=var, Color=color, Quantity=qty,
        Load_Number=load_no, Remarks=rem
    ))
    db.commit()

def log_transfer(db: Session, from_id: str, to_id: str, model: str, var: str, color: str, qty: int, dt: date, rem: str):
    try:
        txn_out = models.InventoryTransaction(
            Date=dt, Transaction_Type=TransactionType.OUTWARD_TRANSFER,
            Current_Branch_ID=from_id, To_Branch_ID=to_id,
            Model=model, Variant=var, Color=color, Quantity=qty,
            Remarks=f"Transfer OUT to {to_id} from {from_id}. {rem}"
        )
        txn_in = models.InventoryTransaction(
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
    db.add(models.InventoryTransaction(
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
            db.add(models.InventoryTransaction(
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
        is_internal = db.query(models.Branch).filter(models.Branch.Branch_ID == source).first()
        
        if is_internal:
            # If internal, every item is a transfer from 'source' to 'current_branch_id'
             for item in vehicle_batch:
                # 1. Outward from source
                db.add(models.InventoryTransaction(
                    Date=date_val, Transaction_Type=TransactionType.OUTWARD_TRANSFER,
                    Current_Branch_ID=source, To_Branch_ID=current_branch_id,
                    Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity'],
                    Remarks=f"Bulk Transfer OUT to {current_branch_id}. {remarks}"
                ))
                # 2. Inward to current
                db.add(models.InventoryTransaction(
                    Date=date_val, Transaction_Type=TransactionType.INWARD_TRANSFER,
                    Current_Branch_ID=current_branch_id, From_Branch_ID=source,
                    Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity'],
                    Remarks=f"Bulk Transfer IN from {source}. {remarks}", Load_Number=load_no
                ))
        else:
            # External OEM inward
            for item in vehicle_batch:
                db.add(models.InventoryTransaction(
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
            db.add(models.InventoryTransaction(
                Date=date_val, Transaction_Type=TransactionType.OUTWARD_TRANSFER,
                Current_Branch_ID=from_branch_id, To_Branch_ID=to_branch_id,
                Remarks=f"Transfer OUT to {to_branch_id}. {remarks}",
                Model=item['Model'], Variant=item['Variant'], Color=item['Color'], Quantity=item['Quantity']
            ))
            # 2. Inward record
            db.add(models.InventoryTransaction(
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
    # Check if source is an internal branch ID
    is_internal = db.query(models.Branch).filter(models.Branch.Branch_ID == source).first()
    
    if is_internal:
        log_transfer(db, source, current_branch_id, model, variant, color, qty, date_val, f"{remarks} (Logged via Inward)")
    else:
        # It's truly external (OEM), just add stock
        txn = models.InventoryTransaction(
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

# ---
# --- NEW FUNCTIONS FOR SALES LIFECYCLE ---
# ---

def get_users_by_role(db: Session, role: str) -> List[models.User]:
    """Retrieves all users matching a specific role."""
    return db.query(models.User).filter(models.User.role == role).all()

def get_sales_records_by_status(db: Session, status: str, branch_id: str = None) -> pd.DataFrame:
    """Gets all sales records matching a fulfillment_status."""
    query = db.query(models.SalesRecord).filter(models.SalesRecord.fulfillment_status == status)
    if branch_id:
        query = query.filter(models.SalesRecord.Branch_ID == branch_id)
    return pd.read_sql(query.statement, db.get_bind())

def get_sales_records_by_statuses(db: Session, statuses: List[str], branch_id: str = None) -> pd.DataFrame:
    """Gets all sales records matching a list of fulfillment_statuses."""
    query = db.query(models.SalesRecord).filter(models.SalesRecord.fulfillment_status.in_(statuses))
    if branch_id:
        query = query.filter(models.SalesRecord.Branch_ID == branch_id)
    return pd.read_sql(query.statement, db.get_bind())

def get_sales_records_for_mechanic(db: Session, mechanic_username: str, branch_id: str = None) -> pd.DataFrame:
    """Gets tasks for a specific mechanic."""
    query = db.query(models.SalesRecord).filter(
        models.SalesRecord.pdi_assigned_to == mechanic_username,
        models.SalesRecord.fulfillment_status == 'PDI In Progress'
    )
    if branch_id:
        query = query.filter(models.SalesRecord.Branch_ID == branch_id)
    return pd.read_sql(query.statement, db.get_bind())

def assign_pdi_mechanic(db: Session, sale_id: int, mechanic_name: str):
    """Assigns a PDI task to a mechanic."""
    try:
        record = db.query(models.SalesRecord).filter(models.SalesRecord.id == sale_id).first()
        if record:
            record.pdi_assigned_to = mechanic_name
            record.fulfillment_status = "PDI In Progress"
            db.commit()
    except Exception as e:
        db.rollback()
        raise e

def complete_pdi(db: Session, sale_id: int,chassis_no: str):
    """Marks PDI as complete and records Engine/Chassis numbers."""
    try:
        record = db.query(models.SalesRecord).filter(models.SalesRecord.id == sale_id).first()
        if record:
            record.chassis_no = chassis_no
            record.fulfillment_status = "PDI Complete"
            record.pdi_completion_date = datetime.now(IST_TIMEZONE)
            db.commit()
    except Exception as e:
        db.rollback()
        raise e

def update_insurance_tr_status(db: Session, sale_id: int, updates: Dict[str, Any]):
    """Updates the Insurance, TR, Dues, and Tax flags."""
    try:
        record = db.query(models.SalesRecord).filter(models.SalesRecord.id == sale_id).first()
        if record:
            # Apply all updates from the dictionary
            for key, value in updates.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            
            # Update fulfillment status based on progression
            if record.is_tr_done:
                record.fulfillment_status = "TR Done"
            elif record.is_insurance_done:
                record.fulfillment_status = "Insurance Done"
                
            db.commit()
    except Exception as e:
        db.rollback()
        raise e