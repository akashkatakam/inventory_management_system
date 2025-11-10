import streamlit as st
import pandas as pd
from datetime import date
from database import get_db
import inventory_manager as mgr
from inventory_models import User 

# --- PAGE CONFIG ---
st.set_page_config(page_title="Inventory Management", layout="wide")

# --- SESSION STATE INITIALIZATION ---
if 'inward_batch' not in st.session_state: st.session_state.inward_batch = []
if 'transfer_batch' not in st.session_state: st.session_state.transfer_batch = []
if 'sales_batch' not in st.session_state: st.session_state.sales_batch = []
if 'inventory_logged_in' not in st.session_state: st.session_state.inventory_logged_in = False
if 'inventory_user_role' not in st.session_state: st.session_state.inventory_user_role = None

# --- HELPER FUNCTIONS ---
@st.cache_data(ttl=3600)
def load_config_data():
    """Loads branches and vehicle master data once."""
    db = next(get_db())
    try:
        all_branches = mgr.get_all_branches(db)
        head_branches = mgr.get_head_branches(db)
        vehicle_master = mgr.get_vehicle_master_data(db)
        return all_branches, head_branches, vehicle_master
    finally:
        db.close()

def vehicle_selection_ui(vehicle_master, key_prefix):
    c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
    model_list = sorted(vehicle_master.keys()) if vehicle_master else ["No Models"]
    model = c1.selectbox("Model", options=model_list, key=f"{key_prefix}_model")
    var_opts = sorted(vehicle_master.get(model, {}).keys()) if model else []
    variant = c2.selectbox("Variant", options=var_opts, key=f"{key_prefix}_variant")
    col_opts = vehicle_master.get(model, {}).get(variant, []) if variant else []
    color = c3.selectbox("Color", options=col_opts, key=f"{key_prefix}_color")
    qty = c4.number_input("Qty", min_value=1, value=1, key=f"{key_prefix}_qty")
    return model, variant, color, qty

def display_batch(batch_key, submit_callback):
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

# --- STOCK VIEW RENDERER (Used by both Public and PDI views) ---
def render_stock_view_interactive(initial_head_name=None, is_public=False):
    
    db = next(get_db())
    try:
        # 1. If public, allow selecting ANY head branch. If private, use the pre-selected one.
        if is_public:
            head_branches = mgr.get_head_branches(db)
            head_map_local = {b.Branch_Name: b.Branch_ID for b in head_branches}
            current_head_name = st.selectbox("Select Territory (Head Branch):", options=head_map_local.keys())
            current_head_id = head_map_local[current_head_name]
        else:
            current_head_name = initial_head_name
            # Find ID from global map loaded in main logic
            current_head_id = head_map[current_head_name]

        st.divider()
        
        # 2. Load all branches managed by this head for the pills
        managed_branches = mgr.get_managed_branches(db, current_head_id)
        managed_map_local = {b.Branch_Name: b.Branch_ID for b in managed_branches}
        all_managed_names = list(managed_map_local.keys())

        # 3. Branch Selection Pills (Default to ALL)
        selected_branches = st.pills(
            f"Filter {current_head_name} Territory by Branch:", 
            options=all_managed_names, 
            selection_mode="multi", 
            default=all_managed_names,
            key=f"stock_pills_{'pub' if is_public else 'priv'}"
        )
        
        if st.button("🔄 Refresh Data", key=f"refresh_{'pub' if is_public else 'priv'}"):
            st.cache_data.clear()
            st.rerun()

        if not selected_branches:
            st.info("Please select at least one branch to view stock.")
            return

        # 4. Fetch Data & Render (Reusing your drill-down logic)
        selected_ids = [managed_map_local[name] for name in selected_branches]
        raw_stock_df = mgr.get_multi_branch_stock(db, selected_ids)
        
        if raw_stock_df.empty:
                st.info("Zero stock recorded across selected branches.")
        else:
            total_vehicles = int(raw_stock_df['Stock'].sum())
            st.metric("Total Territory Stock", f"{total_vehicles}")
            st.markdown("### Detailed Stock Breakdown")

            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("**1. By Model**")
                model_df = raw_stock_df.groupby('Model')['Stock'].sum().reset_index()
                model_df.columns = ['Model', 'Total Qty']
                sel_model = st.dataframe(model_df, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True, height=350)
                selected_model_name = model_df.iloc[sel_model.selection.rows[0]]['Model'] if sel_model.selection.rows else None
            with c2:
                st.markdown("**2. By Variant**")
                if selected_model_name:
                    var_df = raw_stock_df[raw_stock_df['Model'] == selected_model_name]
                    var_summary = var_df.groupby('Variant')['Stock'].sum().reset_index()
                    var_summary.columns = ['Variant', 'Qty']
                    sel_var = st.dataframe(var_summary, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True, height=350)
                    selected_variant_name = var_summary.iloc[sel_var.selection.rows[0]]['Variant'] if sel_var.selection.rows else None
                else:
                    st.caption("👈 Select a Model")
                    selected_variant_name = None
            with c3:
                st.markdown("**3. By Color & Branch**")
                if selected_model_name and selected_variant_name:
                    col_df = raw_stock_df[(raw_stock_df['Model'] == selected_model_name) & (raw_stock_df['Variant'] == selected_variant_name)]
                    col_pivot = col_df.pivot_table(index='Color', columns='Branch_Name', values='Stock', fill_value=0).astype(int)
                    col_pivot['TOTAL'] = col_pivot.sum(axis=1)
                    st.dataframe(col_pivot, use_container_width=True, height=350)
                elif selected_model_name:
                        st.caption("👈 Select a Variant")
                else:
                        st.caption("Wait for selections...")


    except Exception as e:
        st.error(f"Error loading stock data: {e}")
    finally:
        db.close()

# =========================================
# MAIN APP LOGIC
# =========================================

try:
    all_branches, head_branches, vehicle_master = load_config_data()
    all_branch_map = {b.Branch_Name: b.Branch_ID for b in all_branches}
    head_map = {b.Branch_Name: b.Branch_ID for b in head_branches}
except Exception as e:
    st.error(f"Database Connection Failed: {e}")
    st.stop()

# --- SIDEBAR: AUTHENTICATION ---
with st.sidebar:
    st.header("🔐 PDI Login")
    if not st.session_state.inventory_logged_in:
        with st.form("pdi_login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Login", type="primary", use_container_width=True):
                db = next(get_db())
                user = db.query(User).filter(User.username == username).first()
                db.close()
                if user and user.verify_password(password) and user.role in ['Owner', 'PDI']:
                    st.session_state.inventory_logged_in = True
                    st.session_state.inventory_user_role = user.role
                    st.rerun()
                else:
                    st.error("Invalid credentials.")
    else:
        st.success(f"Logged in as: **{st.session_state.inventory_user_role}**")
        if st.button("Logout", type="primary", use_container_width=True):
            st.session_state.inventory_logged_in = False
            st.rerun()
        st.markdown("---")

# =========================================
# VIEW 1: PUBLIC STOCK VIEWER
# =========================================
if not st.session_state.inventory_logged_in:
    st.title("📊 Stock Viewer")
    # Use the new interactive renderer in public mode
    render_stock_view_interactive(is_public=True)

# =========================================
# VIEW 2: PDI OPERATIONS
# =========================================
else:
    st.title("🚚 PDI Operations Center")
    with st.sidebar:
        st.header("📍 Operational Setup")
        current_head_name = st.selectbox("Select Head Branch:", options=head_map.keys())
        current_head_id = head_map[current_head_name]
        st.divider()
        st.info(f"Managing: **{current_head_name}**")
        
        db = next(get_db())
        managed_branches = mgr.get_managed_branches(db, current_head_id)
        db.close()
        managed_map = {b.Branch_Name: b.Branch_ID for b in managed_branches}
        sub_branch_map = {k: v for k, v in managed_map.items() if v != current_head_id}

    tab1, tab2, tab3, tab4 = st.tabs(["📥 OEM Inward", "📤 Branch Transfer", "💰 Record Sales", "📊 Stock View"])

    # === TAB 1: OEM INWARD ===
    with tab1:
        st.header(f"Stock Arrival at {current_head_name}")
        with st.container(border=True):
            c1, c2 = st.columns(2)
            source_options = ["HMSI (OEM)", "Other External"] + sorted([b for b in all_branch_map.keys() if b != current_head_name])
            source_in_name = c1.selectbox("Received From:", options=source_options)
            load_no = c2.text_input("Load / Invoice Number:")
            date_in = c1.date_input("Date Received:", value=date.today())
            remarks_in = c2.text_input("Remarks for entire batch:")
        st.subheader("Add Vehicles")
        model, variant, color, qty = vehicle_selection_ui(vehicle_master, "in")
        if st.button("⬇️ Add to Inward Batch"):
            st.session_state.inward_batch.append({'Model': model, 'Variant': variant, 'Color': color, 'Quantity': qty})
            st.success(f"Added {qty} {model} to batch.")
            st.rerun()
        def submit_inward(batch):
            db = next(get_db())
            try:
                source_val = all_branch_map.get(source_in_name, source_in_name)
                mgr.log_bulk_inward(db, current_head_id, source_val, load_no, date_in, remarks_in, batch)
                st.success(f"Successfully logged {len(batch)} items arriving at {current_head_name}!")
                st.session_state.inward_batch = []
                st.cache_data.clear() 
                st.rerun()
            except Exception as e: st.error(f"Error: {e}")
            finally: db.close()
        display_batch('inward_batch', submit_inward)

    # === TAB 2: OUTWARD TRANSFER ===
    with tab2:
        st.header("Transfer to Sub-Dealer")
        if not sub_branch_map:
            st.warning(f"No sub-branches configured for {current_head_name}.")
        else:
            with st.container(border=True):
                c1, c2 = st.columns(2)
                dest_name = c1.selectbox("Destination Branch:", options=sub_branch_map.keys())
                date_out = c2.date_input("Transfer Date:", value=date.today())
                remarks_out = st.text_input("Transfer Remarks:")
            st.subheader("Add Vehicles")
            model, variant, color, qty = vehicle_selection_ui(vehicle_master, "out")
            if st.button("⬇️ Add to Transfer Batch"):
                st.session_state.transfer_batch.append({'Model': model, 'Variant': variant, 'Color': color, 'Quantity': qty})
                st.success(f"Added {qty} {model} to transfer list.")
                st.rerun()
            def submit_transfer(batch):
                db = next(get_db())
                try:
                    mgr.log_bulk_transfer(db, current_head_id, sub_branch_map[dest_name], date_out, remarks_out, batch)
                    st.success(f"Transferred {len(batch)} items to {dest_name}!")
                    st.session_state.transfer_batch = []
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e: st.error(f"Error: {e}")
                finally: db.close()
            display_batch('transfer_batch', submit_transfer)

    # === TAB 3: RECORD SALES ===
    with tab3:
        st.header("Record Manual Sales")
        with st.container(border=True):
            c1, c2 = st.columns(2)
            sale_branch_name = c1.selectbox("Sold By Branch:", options=managed_map.keys())
            date_sale = c2.date_input("Sale Date:", value=date.today())
            remarks_sale = st.text_input("Sales Remarks:")
        st.subheader("Add Vehicles")
        model, variant, color, qty = vehicle_selection_ui(vehicle_master, "sale")
        if st.button("⬇️ Add to Sales Batch"):
            st.session_state.sales_batch.append({'Model': model, 'Variant': variant, 'Color': color, 'Quantity': qty})
            st.success(f"Added {qty} {model} to sales list.")
            st.rerun()
        def submit_sales(batch):
            db = next(get_db())
            try:
                mgr.log_bulk_sales(db, managed_map[sale_branch_name], date_sale, remarks_sale, batch)
                st.success(f"Recorded {len(batch)} sales for {sale_branch_name}!")
                st.session_state.sales_batch = []
                st.cache_data.clear()
                st.rerun()
            except Exception as e: st.error(f"Error: {e}")
            finally: db.close()
        display_batch('sales_batch', submit_sales)

    # === TAB 4: STOCK VIEW (OPERATIONAL) ===
    with tab4:
        st.header("Operational Stock View")
        # Reuse the same renderer, but lock it to the currently managed head branch territory
        render_stock_view_interactive(initial_head_name=current_head_name, is_public=False)

        # --- NEW: Daily Transfer Summary ---
        st.subheader("🚚 Daily Transfer Summary (Head Office View)")
        try:
            transfer_summary = mgr.get_daily_transfer_summary(db)
            if not transfer_summary.empty:
                # Format date for better readability
                # transfer_summary['Date'] = pd.to_datetime(transfer_summary['Date']).dt.strftime('%Y-%m-%d')
                st.dataframe(
                    transfer_summary, 
                    use_container_width=True, 
                    hide_index=True,
                    height=300
                )
            else:
                st.info("No transfers recorded recently.")
        except Exception as e:
            st.error(f"Error loading transfer summary: {e}")

        st.divider()

        # --- EXISTING: Branch-Specific Recent Activity ---
        st.subheader(f"📜 Detailed Activity for {current_head_name}")
        try:
            # This still shows the raw log for the *selected* branch, which is useful for detailed audits
            hist_df = mgr.get_recent_transactions(db, current_head_id)
            if not hist_df.empty:
                st.dataframe(
                    hist_df[['Date', 'Transaction_Type', 'Model', 'Color', 'Quantity', 'Remarks']], 
                    use_container_width=True, hide_index=True, height=400
                )
            else:
                st.caption("No recent transactions for this branch.")
        except Exception as e:
            st.error(f"Error loading history: {e}")