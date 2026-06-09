import streamlit as st
import geopandas as gpd
import pandas as pd
import ezdxf
from shapely.geometry import Point, LineString, Polygon
from shapely.validation import make_valid
import os
import shutil
import tempfile
import time
import warnings

# ==========================================
# ตั้งค่าหน้าเว็บ
# ==========================================
st.set_page_config(page_title="GeoSpatial Toolkit", page_icon="🌍", layout="wide")
st.title("🌍 GeoSpatial Toolkit (Web App)")
st.markdown("เครื่องมือประมวลผลข้อมูลเชิงพื้นที่: ตัดไฟล์ DXF และแยกไฟล์ DEM (XYZ)")

# ==========================================
# ฟังก์ชันตรรกะหลัก (Core Logic) ของ DXF
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
# ส่วน UI แบ่งเป็น 2 แท็บ
# ==========================================
tab1, tab2 = st.tabs(["✂️ DXF Clipper (3D Z)", "⛰️ DEM → TXT Splitter"])

# ------------------------------------------
# TAB 1: DXF Clipper
# ------------------------------------------
with tab1:
    st.header("เครื่องมือตัดไฟล์ DXF ด้วย Shapefile")
    
    col1, col2 = st.columns(2)
    with col1:
        dxf_file = st.file_uploader("📂 อัปโหลดไฟล์ DXF", type=['dxf'])
    with col2:
        st.info("💡 Shapefile ต้องอัปโหลดรวมกันหลายไฟล์ (เช่น .shp, .shx, .dbf, .prj)")
        shp_files = st.file_uploader("📂 อัปโหลดกลุ่มไฟล์ Shapefile", accept_multiple_files=True)

    if shp_files:
        # สร้างโฟลเดอร์จำลองเพื่อเก็บ Shapefile ชั่วคราวให้ Geopandas อ่านได้
        temp_shp_dir = tempfile.mkdtemp()
        shp_path = None
        for f in shp_files:
            file_path = os.path.join(temp_shp_dir, f.name)
            with open(file_path, "wb") as t:
                t.write(f.read())
            if f.name.lower().endswith(".shp"):
                shp_path = file_path
        
        if shp_path:
            try:
                gdf = gpd.read_file(shp_path)
                columns = [c for c in gdf.columns if c.lower() != 'geometry']
                
                col3, col4 = st.columns(2)
                with col3:
                    selected_col = st.selectbox("📌 เลือกคอลัมน์อ้างอิง (Index):", columns)
                with col4:
                    mode = st.radio("⚙️ โหมดการทำงาน:", ["individual (ราย Polygon)", "group (รวมกลุ่ม)"])
                
                if st.button("🚀 เริ่มตัดไฟล์ DXF", type="primary") and dxf_file:
                    with st.spinner("กำลังประมวลผล กรุณารอสักครู่..."):
                        # บันทึก DXF ชั่วคราว
                        temp_dxf_path = os.path.join(temp_shp_dir, dxf_file.name)
                        with open(temp_dxf_path, "wb") as f:
                            f.write(dxf_file.read())
                            
                        output_dir = tempfile.mkdtemp()
                        
                        # กระบวนการตัด (จำลองจาก Logic เดิม)
                        doc = ezdxf.readfile(temp_dxf_path)
                        out_ver = "R2010" if doc.dxfversion <= "AC1009" else doc.dxfversion
                        msp = doc.modelspace()
                        entity_list = []
                        for entity in msp:
                            geom2d = entity_to_2d_shapely(entity)
                            if geom2d and not geom2d.is_empty:
                                entity_list.append((entity, geom2d, get_vertex_z_list(entity)))
                        
                        mode_clean = "individual" if "individual" in mode else "group"
                        items = list(gdf.iterrows()) if mode_clean == "individual" else list(gdf.groupby(selected_col))
                        
                        progress_bar = st.progress(0)
                        for i, item in enumerate(items, 1):
                            if mode_clean == "individual":
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
                            
                        # สร้างไฟล์ ZIP ให้ผู้ใช้ดาวน์โหลด
                        zip_path = shutil.make_archive(tempfile.mkdtemp() + "/dxf_output", 'zip', output_dir)
                        with open(zip_path, "rb") as fp:
                            st.success("✅ ตัดไฟล์เสร็จสมบูรณ์!")
                            st.download_button(
                                label="⬇️ ดาวน์โหลดผลลัพธ์ (.zip)",
                                data=fp,
                                file_name="dxf_clipped_results.zip",
                                mime="application/zip"
                            )
            except Exception as e:
                st.error(f"เกิดข้อผิดพลาดในการอ่าน Shapefile: {e}")

# ------------------------------------------
# TAB 2: DEM Splitter
# ------------------------------------------
with tab2:
    st.header("เครื่องมือตัดไฟล์ DEM (XYZ) ตาม Shapefile")
    
    xyz_files = st.file_uploader("📂 อัปโหลดไฟล์ XYZ (เลือกได้หลายไฟล์)", type=['xyz', 'txt'], accept_multiple_files=True)
    st.info("💡 Shapefile ต้องอัปโหลดรวมกันหลายไฟล์ (เช่น .shp, .shx, .dbf, .prj)")
    shp_files_dem = st.file_uploader("📂 อัปโหลดกลุ่มไฟล์ Shapefile (สำหรับ DEM)", accept_multiple_files=True, key="dem_shp")

    if shp_files_dem and xyz_files:
        temp_shp_dir = tempfile.mkdtemp()
        shp_path = None
        for f in shp_files_dem:
            file_path = os.path.join(temp_shp_dir, f.name)
            with open(file_path, "wb") as t:
                t.write(f.read())
            if f.name.lower().endswith(".shp"):
                shp_path = file_path
                
        if shp_path:
            gdf = gpd.read_file(shp_path)
            columns = [c for c in gdf.columns if c.lower() != 'geometry']
            selected_col_dem = st.selectbox("📌 เลือก ID column:", columns, key="dem_col")
            
            if st.button("🚀 เริ่มแยกไฟล์ XYZ", type="primary"):
                with st.spinner("กำลังประมวลผลจุด (Point Cloud) กรุณารอสักครู่..."):
                    output_dir = tempfile.mkdtemp()
                    
                    # เตรียม Shapefile
                    clip_gdf = gdf.copy()
                    clip_gdf["_poly_id"] = clip_gdf[selected_col_dem].astype(str).str.strip().str.replace(r'[\s/\\:*?"<>|]', "_", regex=True)
                    minx, miny, maxx, maxy = clip_gdf.total_bounds
                    
                    progress_text = st.empty()
                    progress_bar = st.progress(0)
                    total_files = len(xyz_files)
                    
                    for file_idx, xyz_file in enumerate(xyz_files):
                        fname = os.path.splitext(xyz_file.name)[0]
                        progress_text.text(f"กำลังประมวลผลไฟล์: {xyz_file.name}")
                        
                        sub_dir = os.path.join(output_dir, fname)
                        os.makedirs(sub_dir, exist_ok=True)
                        
                        # อ่านไฟล์ XYZ จากหน่วยความจำ
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
                            with open(out_path, "w") as fout:
                                fout.write("\n".join(lines) + "\n")
                                
                        progress_bar.progress(int(((file_idx + 1) / total_files) * 100))
                        
                    # สร้างไฟล์ ZIP
                    progress_text.text("กำลังบีบอัดไฟล์ผลลัพธ์...")
                    zip_path = shutil.make_archive(tempfile.mkdtemp() + "/dem_output", 'zip', output_dir)
                    
                    with open(zip_path, "rb") as fp:
                        st.success("✅ ประมวลผลและแยกไฟล์เสร็จสมบูรณ์!")
                        progress_text.empty()
                        st.download_button(
                            label="⬇️ ดาวน์โหลดผลลัพธ์ทั้งหมด (.zip)",
                            data=fp,
                            file_name="xyz_split_results.zip",
                            mime="application/zip"
                        )