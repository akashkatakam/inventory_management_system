import streamlit as st
import pandas as pd
from datetime import date
from database import get_db
import inventory_manager as mgr

# --- PAGE CONFIG ---
st.set_page_config(page_title="Inventory Management", layout="wide")
st.title("🚚 Vehicle Inventory Management System")

# --- SESSION STATE INITIALIZATION ---
if 'inward_batch' not in st.session_state: st.session_state.inward_batch = []
if 'transfer_batch' not in st.session_state: st.session_state.transfer_batch = []
if 'sales_batch' not in st.session_state: st.session_state.sales_batch = []

# --- HELPER: DATA LOADING ---
@st.cache_data(ttl=3600)
def load_config_data():
    """Loads branches and vehicle master data once."""
    db = next(get_db())
    try:
        branches = mgr.get_all_branches(db)
        head_branches = mgr.get_head_branches(db)
        vehicle_master = mgr.get_vehicle_master_data(db)
        return branches, head_branches, vehicle_master
    finally:
        db.close()

# --- HELPER: VEHICLE SELECTION UI ---
def vehicle_selection_ui(vehicle_master, key_prefix):
    """Reusable UI component for selecting vehicle details."""
    c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
    
    # 1. Model
    model_list = sorted(vehicle_master.keys()) if vehicle_master else ["No Models"]
    model = c1.selectbox("Model", options=model_list, key=f"{key_prefix}_model")
    
    # 2. Variant (Dependent on Model)
    var_opts = sorted(vehicle_master.get(model, {}).keys()) if model else []
    variant = c2.selectbox("Variant", options=var_opts, key=f"{key_prefix}_variant")
    
    # 3. Color (Dependent on Variant)
    col_opts = vehicle_master.get(model, {}).get(variant, []) if variant else []
    color = c3.selectbox("Color", options=col_opts, key=f"{key_prefix}_color")
    
    # 4. Quantity
    qty = c4.number_input("Qty", min_value=1, value=1, key=f"{key_prefix}_qty")
    
    return model, variant, color, qty

# --- HELPER: BATCH DISPLAY ---
def display_batch(batch_key, submit_callback):
    """Displays the current batch and provides Submit/Clear buttons."""
    batch = st.session_state[batch_key]
    if batch:
        st.markdown("##### 📋 Items in Current Batch")
        st.dataframe(pd.DataFrame(batch), use_container_width=True, hide_index=True)
        
        c1, c2 = st.columns([1, 5])
        if c1.button("🗑️ Clear", key=f"{batch_key}_clear"):
            st.session_state[batch_key] = []
            st.rerun()
            
        if c2.button("✅ Submit Batch", key=f"{batch_key}_submit", type="primary", use_container_width=True):
            submit_callback(batch)

# =========================================
# MAIN APP LOGIC
# =========================================

try:
    all_branches, head_branches, vehicle_master = load_config_data()
    branch_map = {b.Branch_Name: b.Branch_ID for b in all_branches}
    head_map = {b.Branch_Name: b.Branch_ID for b in head_branches}
except Exception as e:
    st.error(f"Database Connection Failed: {e}")
    st.stop()

# --- SIDEBAR ---
with st.sidebar:
    st.header("📍 Location Setup")
    current_head_name = st.selectbox("Select Head Branch:", options=head_map.keys())
    current_head_id = head_map[current_head_name]
    
    st.divider()
    st.info(f"Managing: **{current_head_name}**")
    
    # Load sub-branches for this head dynamically
    db = next(get_db())
    managed_branches = mgr.get_managed_branches(db, current_head_id)
    db.close()
    
    managed_map = {b.Branch_Name: b.Branch_ID for b in managed_branches}
    # Sub-branches only (exclude the head itself for transfer destinations)
    sub_branch_map = {k: v for k, v in managed_map.items() if v != current_head_id}

# --- MAIN TABS ---
tab4, tab2, tab3, tab1 = st.tabs(["📊 Stock View", "📤 Branch Transfer", "💰 Record Sales", "📥 OEM Inward"])

# === TAB 1: OEM INWARD ===
with tab1:
    st.header(f"Stock Arrival at {current_head_name}")
    st.info("Use this tab for stock arriving from HMSI OR transferred from another branch.")
    
    with st.container(border=True):
        c_meta1, c_meta2 = st.columns(2)
        
        # Source list includes HMSI and ALL other branches (Head and Sub)
        other_branches = sorted([b_name for b_name in branch_map.keys() if b_name != current_head_name])
        source_options = ["HMSI (OEM)"] + other_branches
        
        source_in_name = c_meta1.selectbox("Received From:", options=source_options)
        load_no = c_meta2.text_input("Load / Invoice Number (Optional for transfers):")
        date_in = st.date_input("Date Received:", value=date.today())
        remarks_in = st.text_input("Remarks for entire batch:")

    st.subheader("Add Vehicles")
    model, variant, color, qty = vehicle_selection_ui(vehicle_master, "in")
    
    if st.button("⬇️ Add to Inward Batch"):
        st.session_state.inward_batch.append({
            'Model': model, 'Variant': variant, 'Color': color, 'Quantity': qty
        })
        st.success(f"Added {qty} {model} to batch.")
        st.rerun()

    def submit_inward(batch):
        db = next(get_db())
        try:
            # Resolve source name to ID if it's a branch
            source_val = branch_map.get(source_in_name, source_in_name)
            
            # Use the new bulk inward function that handles internal/external sources automatically
            mgr.log_bulk_inward(db, current_head_id, source_val, load_no, date_in, remarks_in, batch)
            
            st.success(f"Successfully logged {len(batch)} items arriving at {current_head_name} from {source_in_name}!")
            st.session_state.inward_batch = []
            st.cache_data.clear() 
            st.rerun()
        except Exception as e:
            st.error(f"Error logging inward: {e}")
        finally:
            db.close()

    display_batch('inward_batch', submit_inward)

# === TAB 2: OUTWARD TRANSFER ===
with tab2:
    # --- CRITICAL CHANGE: Allow transfer to ANY other branch ---
    # Create list of all branches EXCEPT the current one
    dest_options = [b_name for b_name in branch_map.keys()]
    
    st.header("Transfer to Sub-Dealer")
    if not sub_branch_map:
        st.warning(f"No sub-branches configured for {current_head_name}.")
    else:
        with st.container(border=True):
            c_meta1, c_meta2 = st.columns(2)
            # Destination can ONLY be a sub-branch of the current head
            dest_name = c_meta1.selectbox("Destination Branch:", options=branch_map.keys())
            date_out = c_meta2.date_input("Transfer Date:", value=date.today())
            remarks_out = st.text_input("Transfer Remarks (Driver/Vehicle No):")

        st.subheader("Add Vehicles to Transfer")
        model, variant, color, qty = vehicle_selection_ui(vehicle_master, "out")
        
        if st.button("⬇️ Add to Transfer Batch"):
            st.session_state.transfer_batch.append({
                'Model': model, 'Variant': variant, 'Color': color, 'Quantity': qty
            })
            st.success(f"Added {qty} {model} to transfer list.")
            st.rerun()

        def submit_transfer(batch):
            db = next(get_db())
            try:
                dest_id = sub_branch_map[dest_name]
                # Always transfers FROM current HEAD BRANCH to selected SUB BRANCH
                mgr.log_bulk_transfer(db, current_head_id, dest_id, date_out, remarks_out, batch)
                st.success(f"Successfully transferred {len(batch)} items to {dest_name}!")
                st.session_state.transfer_batch = []
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Error logging transfer: {e}")
            finally:
                db.close()

        display_batch('transfer_batch', submit_transfer)

# === TAB 3: RECORD SALES ===
with tab3:
    st.header("Record Manual Sales")
    st.info("Use this to record end-of-day sales for sub-dealers who don't use the main app.")
    
    with st.container(border=True):
        c_meta1, c_meta2 = st.columns(2)
        sale_branch_name = c_meta1.selectbox("Sold By Branch:", options=managed_map.keys())
        date_sale = c_meta2.date_input("Sale Date:", value=date.today())
        remarks_sale = st.text_input("Sales Remarks (Optional):")

    st.subheader("Add Sold Vehicles")
    model, variant, color, qty = vehicle_selection_ui(vehicle_master, "sale")
    
    if st.button("⬇️ Add to Sales Batch"):
        st.session_state.sales_batch.append({
            'Model': model, 'Variant': variant, 'Color': color, 'Quantity': qty
        })
        st.success(f"Added {qty} {model} to sales list.")

    def submit_sales(batch):
        db = next(get_db())
        try:
            branch_id = managed_map[sale_branch_name]
            mgr.log_bulk_sales(db, branch_id, date_sale, remarks_sale, batch)
            st.success(f"Successfully recorded {len(batch)} sales for {sale_branch_name}!")
            st.session_state.sales_batch = []
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Error recording sales: {e}")
        finally:
            db.close()

    display_batch('sales_batch', submit_sales)

# === TAB 4: STOCK VIEW ===
with tab4:
    st.header(f"Territory Stock: {current_head_name}")
    
    # 1. Branch Selection (Pills for multi-select)
    all_managed_names = list(managed_map.keys())
    selected_branches = st.pills("Filter by Branch:", options=all_managed_names, selection_mode="multi", default=all_managed_names, key="stock_branch_pills")
    
    if st.button("🔄 Refresh Stock Data"):
        st.cache_data.clear()
        st.rerun()

    if not selected_branches:
        st.info("Please select at least one branch to view stock.")
    else:
        selected_ids = [managed_map[name] for name in selected_branches]
        db = next(get_db())
        try:
            # Fetch raw data for all selected branches
            raw_stock_df = mgr.get_multi_branch_stock(db, selected_ids)
            
            if raw_stock_df.empty:
                 st.info("Zero stock recorded across selected branches.")
            else:
                # --- High-Level Metrics ---
                total_vehicles = int(raw_stock_df['Stock'].sum())
                st.metric("Total Territory Stock (Selected Branches)", f"{total_vehicles}")
                st.divider()

                # --- Interactive Cascading Tables ---
                c1, c2, c3 = st.columns(3)

                # Table 1: MODEL Selection
                with c1:
                    st.subheader("1. By Model")
                    model_df = raw_stock_df.groupby('Model')['Stock'].sum().reset_index()
                    model_df.columns = ['Model', 'Total Qty']
                    sel_model = st.dataframe(model_df, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True, height=300)
                    selected_model_name = model_df.iloc[sel_model.selection.rows[0]]['Model'] if sel_model.selection.rows else None

                # Table 2: VARIANT Selection (Filtered by Model)
                with c2:
                    st.subheader("2. By Variant")
                    if selected_model_name:
                        var_df = raw_stock_df[raw_stock_df['Model'] == selected_model_name]
                        var_summary = var_df.groupby('Variant')['Stock'].sum().reset_index()
                        var_summary.columns = ['Variant', 'Qty']
                        sel_var = st.dataframe(var_summary, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True, height=300)
                        selected_variant_name = var_summary.iloc[sel_var.selection.rows[0]]['Variant'] if sel_var.selection.rows else None
                    else:
                        st.caption("👈 Select a Model first")
                        selected_variant_name = None

                # Table 3: COLOR View (Filtered by Model & Variant)
                with c3:
                    st.subheader("3. By Color")
                    if selected_model_name and selected_variant_name:
                        col_df = raw_stock_df[(raw_stock_df['Model'] == selected_model_name) & (raw_stock_df['Variant'] == selected_variant_name)]
                        col_summary = col_df.groupby(['Color', 'Branch_Name'])['Stock'].sum().unstack(fill_value=0)
                        col_summary['TOTAL'] = col_summary.sum(axis=1)
                        st.dataframe(col_summary, use_container_width=True, height=300)
                    elif selected_model_name:
                        st.caption("👈 Select a Variant")
                    else:
                        st.caption("Wait for selections...")

        except Exception as e:
            st.error(f"Error loading stock data: {e}")
        finally:
            db.close()