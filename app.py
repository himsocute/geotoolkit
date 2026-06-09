import streamlit as st
import geopandas as gpd
import pandas as pd
import ezdxf
from shapely.geometry import Point, LineString, Polygon
from shapely.validation import make_valid
import os
import shutil
import tempfile
import warnings

# ==========================================
# 1. ตั้งค่าหน้าเว็บ และฝัง Custom CSS
# ==========================================
st.set_page_config(page_title="GeoSpatial Toolkit", page_icon="🌍", layout="centered")

# ฝัง CSS เพื่อแต่งหน้าตาให้เหมือน Replit (Dark/Green)
st.markdown("""
    <style>
    /* พื้นหลังหลักและตัวอักษร */
    .stApp {
        background-color: #0b0f19;
        color: #f8fafc;
    }
    /* สีหัวข้อต่างๆ */
    h1, h2, h3 {
        color: #10b981 !important;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }
    /* กรอบ Uploader */
    [data-testid="stFileUploadDropzone"] {
        background-color: #1e293b !important;
        border: 1.5px dashed #475569 !important;
        border-radius: 12px !important;
        padding: 20px !important;
    }
    [data-testid="stFileUploadDropzone"]:hover {
        border-color: #10b981 !important;
        background-color: #0f172a !important;
    }
    /* ปุ่ม Primary (ปุ่ม Start) */
    .stButton>button[kind="primary"] {
        background-color: #10b981;
        color: white;
        border-radius: 8px;
        border: none;
        padding: 1rem 2rem;
        font-weight: bold;
        width: 100%;
        transition: all 0.3s ease;
    }
    .stButton>button[kind="primary"]:hover {
        background-color: #059669;
        box-shadow: 0 4px 12px rgba(16, 185, 129, 0.4);
    }
    /* กรอบตัวเลือก Radio / Selectbox */
    .stSelectbox, .stRadio {
        background-color: #111827;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #1f2937;
    }
    /* แถบแจ้งเตือน */
    .stAlert {
        border-radius: 8px;
    }
    </style>
""", unsafe_allow_html=True)

st.title("🌍 GeoSpatial Toolkit")
st.markdown("Precision data processing for GIS engineers. Clip DXF drawings and split DEM point clouds by shapefile boundaries.")
st.markdown("---")

# ==========================================
# 2. ฟังก์ชันตรรกะหลัก (DXF Logic)
# ==========================================
def entity_to_2d_shapely(entity):
    t = entity.dxftype()
    try:
        if t == "POLYLINE":
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices if hasattr(v.dxf, "location")]
            if len(pts) < 2: return None
            if getattr(entity, 'is_closed', False) and len(pts) >= 3: return Polygon(pts)
            return LineString(pts)
        elif t == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in entity.get_points('xy')]
            if len(pts) < 2: return None
            if getattr(entity, 'closed', False) and len(pts) >= 3: return Polygon(pts)
            return LineString(pts)
        elif t == "LINE":
            s, e = entity.dxf.start, entity.dxf.end
            return LineString([(s.x, s.y), (e.x, e.y)])
    except: pass
    return None

def get_vertex_z_list(entity):
    t = entity.dxftype()
    z_list = []
    try:
        if t == "POLYLINE":
            for v in entity.vertices: z_list.append(float(getattr(v.dxf.location, 'z', 0.0)))
        elif t == "LWPOLYLINE":
            elev = float(entity.dxf.elevation) if entity.dxf.hasattr("elevation") else 0.0
            count = len(list(entity.get_points('xy')))
            z_list = [elev] * count
        elif t == "LINE":
            z_list = [float(getattr(entity.dxf.start, 'z', 0.0)), float(getattr(entity.dxf.end, 'z', 0.0))]
    except: pass
    return z_list

def write_clipped_geom(geom, src_entity, new_msp, original_z_list):
    if geom is None or geom.is_empty: return 0
    attribs = {"layer": src_entity.dxf.layer}
    count = [0]
    def _write(g):
        gtype = g.geom_type
        try:
            if gtype in ("LineString", "LinearRing"):
                coords = list(g.coords)
                n = len(coords)
                zs = original_z_list[:n] if len(original_z_list) >= n else [original_z_list[0] if original_z_list else 0.0] * n
                pts = [(x, y, z) for (x, y), z in zip(coords, zs)]
                pl = new_msp.add_polyline3d(pts, dxfattribs=attribs)
                if gtype == "LinearRing" or getattr(src_entity, 'is_closed', False): pl.closed = True
                count[0] += 1
            elif gtype == "Polygon":
                ext = list(g.exterior.coords)
                zs = original_z_list[:len(ext)] if len(original_z_list) >= len(ext) else [original_z_list[0] if original_z_list else 0.0] * len(ext)
                pts = [(x, y, z) for (x, y), z in zip(ext, zs)]
                pl = new_msp.add_polyline3d(pts, dxfattribs=attribs)
                pl.closed = True
                count[0] += 1
            elif gtype in ("MultiLineString", "MultiPolygon", "GeometryCollection"):
                for part in g.geoms: _write(part)
        except: pass
    _write(geom)
    return count[0]

def safe_filename(name):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in str(name)).strip("_") or "unnamed"

# ==========================================
# 3. สร้างระบบ TAB แบบ Modern
# ==========================================
tab_dxf, tab_dem = st.tabs(["✂️ DXF CLIPPER", "⛰️ DEM SPLITTER"])

# ------------------------------------------
# หน้า DXF CLIPPER
# ------------------------------------------
with tab_dxf:
    st.subheader("1. Upload Files (อัปโหลดไฟล์)")
    
    col_dxf, col_shp = st.columns(2)
    with col_dxf:
        st.caption("DXF Drawing")
        dxf_file = st.file_uploader("Click to select DXF file (.dxf only)", type=['dxf'], key="dxf_up")
    with col_shp:
        st.caption("Shapefile Boundary Group")
        shp_files = st.file_uploader("Click to select multiple files (.shp, .shx, .dbf, .prj)", accept_multiple_files=True, key="shp_up")

    st.markdown("---")
    st.subheader("2. Configure & Run (ตั้งค่าและประมวลผล)")

    if shp_files:
        temp_shp_dir = tempfile.mkdtemp()
        shp_path = None
        for f in shp_files:
            file_path = os.path.join(temp_shp_dir, f.name)
            with open(file_path, "wb") as t: t.write(f.read())
            if f.name.lower().endswith(".shp"): shp_path = file_path
        
        if shp_path:
            try:
                gdf = gpd.read_file(shp_path)
                columns = [c for c in gdf.columns if c.lower() != 'geometry']
                
                selected_col = st.selectbox("Reference Column (คอลัมน์อ้างอิง)", columns)
                
                # ตัวเลือก Radio แบบใหม่
                mode = st.radio(
                    "Output Mode (รูปแบบผลลัพธ์)", 
                    [
                        "🟢 Individual Polygons (One output file per polygon feature)", 
                        "⚪ Group by Column Value (Merge polygons with the same column value)"
                    ]
                )
                
                st.markdown("<br>", unsafe_allow_html=True)
                
                if st.button("▶ START CLIPPING", type="primary") and dxf_file:
                    with st.spinner("⏳ Processing... Please wait"):
                        temp_dxf_path = os.path.join(temp_shp_dir, dxf_file.name)
                        with open(temp_dxf_path, "wb") as f: f.write(dxf_file.read())
                        output_dir = tempfile.mkdtemp()
                        
                        doc = ezdxf.readfile(temp_dxf_path)
                        out_ver = "R2010" if doc.dxfversion <= "AC1009" else doc.dxfversion
                        msp = doc.modelspace()
                        entity_list = []
                        for entity in msp:
                            geom2d = entity_to_2d_shapely(entity)
                            if geom2d and not geom2d.is_empty:
                                entity_list.append((entity, geom2d, get_vertex_z_list(entity)))
                        
                        is_individual = "Individual" in mode
                        items = list(gdf.iterrows()) if is_individual else list(gdf.groupby(selected_col))
                        
                        progress_bar = st.progress(0)
                        for i, item in enumerate(items, 1):
                            if is_individual:
                                clip_poly = item[1].geometry
                                idx_val = str(item[1][selected_col]).strip()
                            else:
                                clip_poly = item[1].geometry.unary_union
                                idx_val = str(item[0]).strip()
                                
                            fname = safe_filename(idx_val) + ".dxf"
                            out_path = os.path.join(output_dir, fname)
                            
                            if clip_poly and clip_poly.is_valid:
                                new_doc = ezdxf.new(out_ver)
                                new_msp = new_doc.modelspace()
                                for entity, geom2d, z_list in entity_list:
                                    if geom2d.intersects(clip_poly):
                                        clipped = geom2d.intersection(clip_poly)
                                        write_clipped_geom(clipped, entity, new_msp, z_list)
                                new_doc.saveas(out_path)
                            progress_bar.progress(int((i / len(items)) * 100))
                            
                        zip_path = shutil.make_archive(tempfile.mkdtemp() + "/dxf_output", 'zip', output_dir)
                        with open(zip_path, "rb") as fp:
                            st.success("✅ Process Completed Successfully!")
                            st.download_button(label="⬇️ Download Results (.zip)", data=fp, file_name="dxf_clipped_results.zip", mime="application/zip")
            except Exception as e:
                st.error(f"Error reading shapefile: {e}")
    else:
        st.info("⚠️ Please upload a Shapefile group to unlock configuration options.")

# ------------------------------------------
# หน้า DEM SPLITTER
# ------------------------------------------
with tab_dem:
    st.subheader("1. Upload Files (อัปโหลดไฟล์)")
    xyz_files = st.file_uploader("Click to select XYZ/TXT files (Multiple allowed)", type=['xyz', 'txt'], accept_multiple_files=True)
    shp_files_dem = st.file_uploader("Click to select Shapefile group (.shp, .shx, .dbf, .prj)", accept_multiple_files=True, key="dem_shp")

    st.markdown("---")
    st.subheader("2. Configure & Run (ตั้งค่าและประมวลผล)")

    if shp_files_dem and xyz_files:
        temp_shp_dir = tempfile.mkdtemp()
        shp_path = None
        for f in shp_files_dem:
            file_path = os.path.join(temp_shp_dir, f.name)
            with open(file_path, "wb") as t: t.write(f.read())
            if f.name.lower().endswith(".shp"): shp_path = file_path
                
        if shp_path:
            gdf = gpd.read_file(shp_path)
            columns = [c for c in gdf.columns if c.lower() != 'geometry']
            selected_col_dem = st.selectbox("Reference Column (คอลัมน์อ้างอิง)", columns, key="dem_col")
            
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("▶ START SPLITTING", type="primary"):
                with st.spinner("⏳ Processing Point Clouds... Please wait"):
                    output_dir = tempfile.mkdtemp()
                    clip_gdf = gdf.copy()
                    clip_gdf["_poly_id"] = clip_gdf[selected_col_dem].astype(str).str.strip().str.replace(r'[\s/\\:*?"<>|]', "_", regex=True)
                    minx, miny, maxx, maxy = clip_gdf.total_bounds
                    
                    progress_text = st.empty()
                    progress_bar = st.progress(0)
                    total_files = len(xyz_files)
                    
                    for file_idx, xyz_file in enumerate(xyz_files):
                        fname = os.path.splitext(xyz_file.name)[0]
                        progress_text.text(f"Processing: {xyz_file.name}")
                        sub_dir = os.path.join(output_dir, fname)
                        os.makedirs(sub_dir, exist_ok=True)
                        
                        df = pd.read_csv(xyz_file, sep=r'\s+', header=None, names=['x', 'y', 'z'], dtype={'x': 'float64', 'y': 'float64', 'z': 'float32'})
                        gdf_pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df['x'], df['y']), crs=clip_gdf.crs)
                        
                        mask = (gdf_pts['x'] >= minx) & (gdf_pts['x'] <= maxx) & (gdf_pts['y'] >= miny) & (gdf_pts['y'] <= maxy)
                        gdf_filt = gdf_pts[mask].copy()
                        
                        joined = gpd.sjoin(gdf_filt, clip_gdf[['geometry', '_poly_id']], how='inner', predicate='within')
                        groups = list(joined.groupby('_poly_id'))
                        
                        for i, (poly_id, group) in enumerate(groups):
                            out_path = os.path.join(sub_dir, f"{poly_id}.xyz")
                            sub = group[['x', 'y', 'z']]
                            lines = sub['x'].astype(str) + " " + sub['y'].astype(str) + " " + sub['z'].astype(str)
                            with open(out_path, "w") as fout: fout.write("\n".join(lines) + "\n")
                                
                        progress_bar.progress(int(((file_idx + 1) / total_files) * 100))
                        
                    zip_path = shutil.make_archive(tempfile.mkdtemp() + "/dem_output", 'zip', output_dir)
                    with open(zip_path, "rb") as fp:
                        st.success("✅ Process Completed Successfully!")
                        progress_text.empty()
                        st.download_button(label="⬇️ Download Results (.zip)", data=fp, file_name="xyz_split_results.zip", mime="application/zip")
    else:
        st.info("⚠️ Please upload both XYZ files and a Shapefile group to unlock configuration options.")
