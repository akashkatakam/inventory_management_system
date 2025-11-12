import streamlit as st
import pandas as pd
from datetime import date
from database import get_db
import inventory_manager as mgr
from inventory_models import User 
from streamlit_qrcode_scanner import qrcode_scanner

# --- PAGE CONFIG ---
st.set_page_config(page_title="Inventory & PDI", layout="wide")

# --- SESSION STATE INITIALIZATION ---
if 'inward_batch' not in st.session_state: st.session_state.inward_batch = []
if 'transfer_batch' not in st.session_state: st.session_state.transfer_batch = []
if 'sales_batch' not in st.session_state: st.session_state.sales_batch = []
if 'inventory_logged_in' not in st.session_state: st.session_state.inventory_logged_in = False
if 'inventory_user_role' not in st.session_state: st.session_state.inventory_user_role = None
if 'inventory_username' not in st.session_state: st.session_state.inventory_username = None
if 'inventory_branch_id' not in st.session_state: st.session_state.inventory_branch_id = None
if 'inventory_branch_name' not in st.session_state: st.session_state.inventory_branch_name = "N/A"

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
def render_stock_view_interactive(initial_head_name=None, is_public=False, head_map_global={}):
    
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
            current_head_id = head_map_global.get(current_head_name)
            if not current_head_id:
                st.error("Head branch ID not found.")
                return

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

        # 4. Fetch Data & Render
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
# --- NEW: PDI/MECHANIC VIEWS ---
# =========================================

def render_pdi_assignment_view(branch_id=None):
    st.header("PDI Task Assignment")
    db = next(get_db())
    try:
        pending_pdi_data = mgr.get_sales_records_by_status(db, "PDI Pending", branch_id=branch_id)
        
        if pending_pdi_data.empty:
            st.info("No sales are currently pending PDI assignment.")
            return
            
        mechanics = mgr.get_users_by_role(db, "Mechanic")
        mechanic_names = [m.username for m in mechanics]
            
        if not mechanic_names:
            st.error("No 'Mechanic' users found. Please create Mechanic accounts to assign tasks.")
            return

        with st.form("assign_pdi_form"):
            st.subheader("Assign Task")
            
            pending_pdi_data['display'] = pending_pdi_data['DC_Number'] + " (" + pending_pdi_data['Customer_Name'] + " - " + pending_pdi_data['Model'] + ")"
            sale_display_str = st.selectbox("Select Sale Record:", pending_pdi_data['display'])
            
            selected_mechanic = st.selectbox("Assign to Mechanic:", mechanic_names)
            
            submitted = st.form_submit_button("Assign Task")
            
            if submitted:
                sale_id = pending_pdi_data[pending_pdi_data['display'] == sale_display_str].iloc[0]['id']
                mgr.assign_pdi_mechanic(db, int(sale_id), selected_mechanic)
                st.success(f"Task {sale_display_str} assigned to {selected_mechanic}!")
                st.cache_data.clear()
                st.rerun()

        st.subheader("Pending PDI Assignment List")
        st.dataframe(pending_pdi_data[['DC_Number', 'Customer_Name', 'Model', 'Variant', 'Paint_Color', 'Sales_Staff']], use_container_width=True)
    except Exception as e:
        st.error(f"Error: {e}")
    finally:
        db.close()

def render_mechanic_view(username, branch_id=None):
    db = next(get_db())
    try:
        # If username is None (like in PDI admin view), show all 'In Progress' tasks
        if username:
            my_tasks = mgr.get_sales_records_for_mechanic(db, username, branch_id=branch_id)
            st.header("My PDI Tasks")
        else:
            my_tasks = mgr.get_sales_records_by_status(db, "PDI In Progress", branch_id=branch_id)
            st.header("All In-Progress PDI Tasks")


        if my_tasks.empty:
            st.success("No pending tasks. Great job! Tasks will appear here when assigned.")
            return

        my_tasks['display'] = my_tasks['DC_Number'] + " (" + my_tasks['Customer_Name'] + " - " + my_tasks['Model'] + ")"
        
        # Only show the selectbox if in 'Mechanic' mode (username is provided)
        if username:
            task_display_str = st.selectbox("Select Task to Complete:", my_tasks['display'])
        else:
            st.dataframe(my_tasks[['DC_Number', 'Customer_Name', 'Model', 'pdi_assigned_to']], use_container_width=True)
            return # PDI admin doesn't complete tasks, so we stop here
        
        if task_display_str:
            selected_task = my_tasks[my_tasks['display'] == task_display_str].iloc[0]
            sale_id = int(selected_task['id'])
            
            st.subheader(f"Complete Task: {selected_task['DC_Number']}")
            st.write(f"**Customer:** {selected_task['Customer_Name']}")
            st.write(f"**Vehicle:** {selected_task['Model']} / {selected_task['Variant']} / {selected_task['Paint_Color']}")
            
            # --- NEW: QR Code Scanning UI ---
            st.divider()
            st.subheader("Scan Vehicle Details")

            # --- Chassis Number Scanner ---
            st.write("**2. Scan Chassis Number QR Code**")
            # [Image of a QR code]
            scanned_chassis = qrcode_scanner(key="chassis_scanner")
            if scanned_chassis:
                st.session_state.scanned_chassis = scanned_chassis
            
            chassis_val = st.text_input("Chassis Number:", value=st.session_state.scanned_chassis, placeholder="Scan or type Chassis No.")
            
            st.divider()

            # --- Completion Button ---
            if st.button("Mark PDI Complete", type="primary", use_container_width=True):
                if  not chassis_val:
                    st.warning("Engine Number and Chassis Number are required.")
                else:
                    try:
                        mgr.complete_pdi(db, sale_id, chassis_val)
                        st.success("PDI Task completed and vehicle details saved!")
                        st.balloons()
                        # Clear state for next scan
                        st.session_state.scanned_engine = ""
                        st.session_state.scanned_chassis = ""
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to complete task: {e}")
    except Exception as e:
        st.error(f"Error: {e}")
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
    st.error(f"Database Connection Failed: {e}. Check .streamlit/secrets.toml")
    st.stop()

# --- SIDEBAR: AUTHENTICATION ---
with st.sidebar:
    st.header("🔐 PDI & Ops Login")
    if not st.session_state.inventory_logged_in:
        with st.form("pdi_login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Login", type="primary", use_container_width=True):
                db = next(get_db())
                user = db.query(User).filter(User.username == username).first()
                db.close()
                # --- UPDATED: Allow only PDI/Mechanic roles ---
                allowed_roles = ['Owner', 'PDI', 'Mechanic']
                if user and user.verify_password(password) and user.role in allowed_roles:
                    st.session_state.inventory_logged_in = True
                    st.session_state.inventory_user_role = user.role
                    st.session_state.inventory_username = user.username
                    st.session_state.inventory_branch_id = user.Branch_ID
                    # Fetch branch name
                    if user.Branch_ID:
                        for b in all_branches:
                            if b.Branch_ID == user.Branch_ID:
                                st.session_state.inventory_branch_name = b.Branch_Name
                                break
                    st.rerun()
                else:
                    st.error("Invalid credentials or role not permitted.")
    else:
        st.success(f"User: **{st.session_state.inventory_username}**")
        st.info(f"Role: **{st.session_state.inventory_user_role}**")
        st.caption(f"Branch: {st.session_state.inventory_branch_name}")
        
        if st.button("Logout", type="primary", use_container_width=True):
            st.session_state.inventory_logged_in = False
            st.session_state.inventory_user_role = None
            st.session_state.inventory_username = None
            st.session_state.inventory_branch_id = None
            st.session_state.inventory_branch_name = "N/A"
            st.rerun()
        st.markdown("---")

# =========================================
# VIEW 1: PUBLIC STOCK VIEWER
# =========================================
if not st.session_state.inventory_logged_in:
    st.title("📊 Stock Viewer")
    render_stock_view_interactive(is_public=True)

# =========================================
# VIEW 2: LOGGED-IN OPERATIONS
# =========================================
else:
    role = st.session_state.inventory_user_role
    username = st.session_state.inventory_username
    branch_id = st.session_state.inventory_branch_id

    # --- PDI/Owner Role: Full Ops Center ---
    if role in ['Owner', 'PDI']:
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

        # --- UPDATED: Removed Insurance & TR tab ---
        tab_list = [
            "📋 PDI Assignment",
            "🔧 Mechanic Tasks",
            "📊 Stock View",
            "📥 OEM Inward", 
            "📤 Branch Transfer", 
            "💰 Record Sales",
        ]
        
        tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(tab_list)

        with tab1:
            render_pdi_assignment_view(branch_id=branch_id)
        
        with tab2:
            st.info("This is a view of all tasks. Log in as a 'Mechanic' to see your specific queue.")
            render_mechanic_view(username=None, branch_id=branch_id) # Show all tasks if username is None
        
        with tab3:
            st.header("Operational Stock View")
            render_stock_view_interactive(initial_head_name=current_head_name, is_public=False, head_map_global=head_map)
            
            st.subheader("🚚 Daily Transfer Summary (Head Office View)")
            try:
                db = next(get_db())
                transfer_summary = mgr.get_daily_transfer_summary(db)
                if not transfer_summary.empty:
                    st.dataframe(transfer_summary, use_container_width=True, hide_index=True, height=300)
                else:
                    st.info("No transfers recorded recently.")
            except Exception as e:
                st.error(f"Error loading transfer summary: {e}")
            finally:
                db.close()

            st.divider()
            st.subheader(f"📜 Detailed Activity for {current_head_name}")
            try:
                db = next(get_db())
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
            finally:
                db.close()
        
        # --- Existing Inventory Tabs ---
        with tab4:
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

        with tab5:
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

        with tab6:
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

    # --- Mechanic Role: Simple View ---
    elif role == 'Mechanic':
        st.title("🔧 My PDI Tasks")
        render_mechanic_view(username, branch_id)