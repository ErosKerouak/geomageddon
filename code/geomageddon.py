# Built-in
import colorsys
import glob
import html
import json
import math
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

# Third-party
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box
from shapely.ops import unary_union


# ========================= helpers de cor =========================
def _hex_to_rgb(hex_str):
    h = hex_str.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0,2,4))

def _rgb_to_hex(rgb):
    r,g,b = rgb
    return "#{:02X}{:02X}{:02X}".format(max(0,min(255,r)), max(0,min(255,g)), max(0,min(255,b)))

def _rgba_to_hex(rgba_str):
    # extrai os 3 primeiros inteiros (r,g,b) de qualquer string
    nums = re.findall(r'\d+', str(rgba_str))
    if len(nums) >= 3:
        r, g, b = map(int, nums[:3])
        return _rgb_to_hex((r, g, b))
    return ""


def mix_two(c1, c2, a=0.5):
    """interpolação RGB; ignora vazios"""
    if not c1 and not c2: return ""
    if not c1: return c2
    if not c2: return c1
    r1,g1,b1 = _hex_to_rgb(c1); r2,g2,b2 = _hex_to_rgb(c2)
    r = int((1-a)*r1 + a*r2); g = int((1-a)*g1 + a*g2); b = int((1-a)*b1 + a*b2)
    return _rgb_to_hex((r,g,b))

def mix_many(colors: Iterable[str]):
    """média RGB das N cores (ignora vazios)"""
    cols = [c for c in colors if c]
    if not cols: return ""
    rs = []; gs = []; bs = []
    for c in cols:
        r,g,b = _hex_to_rgb(c)
        rs.append(r); gs.append(g); bs.append(b)
    r = int(sum(rs)/len(rs)); g = int(sum(gs)/len(gs)); b = int(sum(bs)/len(bs))
    return _rgb_to_hex((r,g,b))

def jitter(hex_color, k, dh=0.08, dl=0.04):
    """variação leve HLS, reprodutível por índice k"""
    if not hex_color: return ""
    r,g,b = _hex_to_rgb(hex_color)
    r/=255; g/=255; b/=255
    h,l,s = colorsys.rgb_to_hls(r,g,b)
    phi = 0.61803398875
    h = (h + (k+1)*phi*dh) % 1.0
    l = max(0, min(1, l + ((-1)**k)*dl))
    r,g,b = colorsys.hls_to_rgb(h,l,s)
    return _rgb_to_hex((int(r*255), int(g*255), int(b*255)))

def _norm_key(s):
    if s is None: return ""
    t = ''.join(c for c in unicodedata.normalize('NFD', str(s)) if unicodedata.category(c) != 'Mn')
    return t.strip().lower()

def _hex_to_rgba(hex_str, a=255):
    h = hex_str.lstrip("#")
    r = int(h[0:2],16); g = int(h[2:4],16); b = int(h[4:6],16)
    return f"{r},{g},{b},{a}"

def _qml_categorized(attr, categories, outline_rgb="85,85,85,255", outline_w=0.2, geom_type=2):
    """
    categories: [{'value':str,'label':str,'color':'#RRGGBB'}]
    """
    cat_xml = []
    sym_xml = []
    for idx, cat in enumerate(categories):
        val = html.escape(str(cat["value"]))
        lab = html.escape(str(cat.get("label", cat["value"])))
        rgba = _hex_to_rgba(cat["color"], 255)
        cat_xml.append(f'<category symbol="{idx}" value="{val}" label="{lab}" render="true"/>')
        sym_xml.append(
            f'''<symbol name="{idx}" type="fill" force_rhr="0" clip_to_extent="1" alpha="1">
  <layer enabled="1" pass="0" locked="0" class="SimpleFill">
    <prop k="color" v="{rgba}"/>
    <prop k="outline_color" v="{outline_rgb}"/>
    <prop k="outline_width" v="{outline_w}"/>
    <prop k="style" v="solid"/>
    <prop k="outline_style" v="solid"/>
  </layer>
</symbol>'''
        )
    cats = "\n      ".join(cat_xml)
    syms = "\n      ".join(sym_xml)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<qgis styleCategories="Symbology" version="3.28.0">
  <renderer-v2 attr="{html.escape(attr)}" type="categorizedSymbol">
    <categories>
      {cats}
    </categories>
    <symbols>
      {syms}
    </symbols>
  </renderer-v2>
  <layerGeometryType>{geom_type}</layerGeometryType>
</qgis>'''


def mix_weighted(colors, weights):
    """média ponderada em RGB; ignora cores vazias e pesos <=0"""
    pairs = [(c, float(w)) for c, w in zip(colors, weights) if c and float(w) > 0]
    if not pairs:
        return ""
    sw = sum(w for _, w in pairs)
    if sw <= 0:
        return ""
    R = sum(_hex_to_rgb(c)[0] * w for c, w in pairs) / sw
    G = sum(_hex_to_rgb(c)[1] * w for c, w in pairs) / sw
    B = sum(_hex_to_rgb(c)[2] * w for c, w in pairs) / sw
    return _rgb_to_hex((int(round(R)), int(round(G)), int(round(B))))





# ========================= Classe =========================

class GeoSiglaStyler:
    """
    Pipeline modular + uso de .qml:
      - load_sigla_qml(paths) -> lê 1+ QML e monta mapa SIGLA→#hex
      - merge(), classify(), dissolve_by(), save_shp(), make_qml()
      - run_pipeline() com flags

    No make_qml(attr='coarse_grp'):
      1) se houver cores de SIGLA via QML, mistura as cores das siglas de cada grupo para obter o primeiro grp_color;
      2) fallback: ROC/idade; 3) desempates (ROC, R1, jitter).
    """

    def __init__(self,
        # campos
        sigla_field:   str ="SIGLA_UNID",
        name_field:    str = "NOME_UNIDA",
        eon_min_field: str ="EON_IDAD_1",
        eon_max_field: str ="EON_IDAD_M",
        era_min_field: str ="ERA_MINIMA",
        era_max_field: str ="ERA_MAXIMA",
        # estilos / mapas
        idade_code_map: Optional[Dict]= {
            "Pre-cambriano": {
                "Arqueano": {"code": "A", "color": "#F4A460"},
                "Paleoproterozóico": {"code": "PP", "color": "#FFDAB9"},
                "Mesoproterozóico": {"code": "MP", "color": "#FFA07A"},
                "Neoproterozóico": {"code": "NP", "color": "#CD5C5C"}
            },
            "Paleozóico": {
                "Permiano": {"code": "P", "color": "#E6E6E6"},
                "Carbonífero": {"code": "C", "color": "#99CC99"},
                "Devoniano": {"code": "D", "color": "#FFCC99"},
                "Siluriano": {"code": "S", "color": "#FF9999"},
                "Ordoviciano": {"code": "O", "color": "#66CCFF"},
                "Cambriano": {"code": "C_cortado", "color": "#33CCCC"}  # Є
            },
            "Mesozóico": {
                "Cretáceo": {"code": "K", "color": "#FFFF99"},
                "Jurássico": {"code": "J", "color": "#66FF66"},
                "Triássico": {"code": "Tr", "color": "#FF6666"}
            },
            "Cenozóico": {
                "Quaternário": {"code": "Q", "color": "#FFCCFF"},
                "Neógeno": {"code": "N", "color": "#FF9900"},
                "Paleógeno": {"code": "Pg", "color": "#FFFF66"}
            }
        },
        # dict rico {"era":{"nome":{"code","color"}}}
        classe_roc_colors: Optional[Dict]= {
            "Material superficial": "#CCCC99",
            "Sedimentar": "#FFCC66",
            "Metamórfica": "#CC99FF",
            "Ígnea": "#9999FF",
            "Ígnea, Metamórfica": "#9966CC",
            "Metamórfica, Ígnea": "#9966CC",
            "Sedimentar, Ígnea": "#FF9966",
            "Ígnea, Sedimentar": "#FF9966",
            "Sedimentar (ou Sedimentos)": "#FFCC66",
            "Ígnea, Sedimentar (ou Sedimentos)": "#FF9966"
        },
        # {texto:"#RRGGBB"}
        classe_r1_colors: Optional[Dict]= {
            "intrusiva": "#8FA1FF",
            "extrusiva": "#A9B8FF",
            "clástica": "#FFDD99",
            "química": "#FFE8B3",
            "regional": "#BFA1FF",
            "contato": "#D5B8FF"
        },
        # {texto:"#RRGGBB"}
        area_crs: Optional[str]=None,
        area_weighting: bool=True,
        # opções de estilo
        outline_color="#555555",
        outline_width=0.2,
        # parsing
        stem_len=2,
        ignore_allcaps=("C","D","E","A","B"),
        fig_width: float = 180.0,          # largura da figura (padrão 180 mm)
        width_unit: str = "mm",            # "mm" | "cm" | "in" | "px"
        dpi: Optional[int] = 300,          # usado apenas se width_unit="px"
        margin: float = 0.0,               # margem em cada lado
        margin_unit: str = "mm",           # unidade da margem
        scale_round_to: Optional[str] = "nice",  # None | "nice"
        scale_round_mode: str = "ceil",    # "ceil" | "nearest"
        auto_cull_small_parts: bool = True,# aplicar poda automática ao final do combine
        min_area_mm2: float = 1.0          # limiar de visibilidade (1 mm²)
    ):


        self.name_field = name_field
        self.sigla_field = sigla_field
        self.eon_min_field = eon_min_field
        self.eon_max_field = eon_max_field
        self.era_min_field = era_min_field
        self.era_max_field = era_max_field

        self.idade_code_map_rich = idade_code_map or {}
        self.classe_roc_colors = classe_roc_colors or {}
        self.classe_r1_colors  = classe_r1_colors  or {}

        self.outline_color = outline_color
        self.outline_width = outline_width

        self.stem_len = stem_len
        self.ignore_allcaps = set(ignore_allcaps)

        # mapa opcional abastecido por QML: {SIGLA (string) -> "#RRGGBB"}
        self.sigla_color_map: Dict[str,str] = {}

        self.g_base = None
        self.g_clipped = None
        self.color_map = None
        self.color_audit = None
        self.legend_dict = None

        # 2) NO __init__ DA CLASSE, acrescente estes parâmetros e atribuições:
        #   def __init__(..., area_crs: Optional[str]=None, area_weighting: bool=True, ...):
        self.area_crs = area_crs
        self.area_weighting = area_weighting
        self.qml_palettes = []  # lista de dicts: {"attr": "<nome_do_campo>", "map": {valor:"#RRGGBB"}, "n": int}

        # ===== NOVOS ATRIBUTOS DE FIGURA/ESCALA =====
        self.fig_width = float(fig_width)
        self.width_unit = str(width_unit)
        self.dpi = None if dpi is None else int(dpi)
        self.margin = float(margin)
        self.margin_unit = str(margin_unit)
        self.scale_round_to = (None if scale_round_to is None else str(scale_round_to))
        self.scale_round_mode = str(scale_round_mode)
        self.auto_cull_small_parts = bool(auto_cull_small_parts)
        self.min_area_mm2 = float(min_area_mm2)

        # validações simples
        if self.width_unit not in {"mm","cm","in","px"}:
            raise ValueError("width_unit deve ser 'mm', 'cm', 'in' ou 'px'.")
        if self.margin_unit not in {"mm","cm","in","px"}:
            raise ValueError("margin_unit deve ser 'mm', 'cm', 'in' ou 'px'.")
        if self.width_unit == "px" and (self.dpi is None or self.dpi <= 0):
            raise ValueError("Para width_unit='px', forneça 'dpi' > 0.")
        if self.scale_round_to not in {None, "nice"}:
            raise ValueError("scale_round_to deve ser None ou 'nice'.")
        if self.scale_round_mode not in {"ceil", "nearest"}:
            raise ValueError("scale_round_mode deve ser 'ceil' ou 'nearest'.")
        if self.min_area_mm2 <= 0:
            raise ValueError("min_area_mm2 deve ser > 0.")

    def combine_and_classify(self,
                            gdfs: Optional[List[gpd.GeoDataFrame]] = None,
                            in_gdf: Optional[gpd.GeoDataFrame] = None,
                            fields_to_keep: Optional[List[str]] = None,
                            enforce_mode: str = "flag",

                            ) -> gpd.GeoDataFrame:
        """
        Combina (se gdfs) e classifica (coarse_grp etc.), SEM dissolver.
        Se cull_small_parts for None, usa self.auto_cull_small_parts.
        """
        if gdfs and in_gdf is not None:
            raise ValueError("Use 'gdfs' OU 'in_gdf', não ambos.")
        if gdfs:
            g = self.merge(gdfs)
        elif in_gdf is not None:
            g = in_gdf.copy()
        else:
            raise ValueError("Forneça 'gdfs' ou 'in_gdf'.")

        must = set(fields_to_keep or [])
        must.update({"SIGLA_UNID","NOME_UNIDA","HIERARQUIA"})  # para legenda
        g = self.classify(g, enforce_mode=enforce_mode, fields_to_keep=sorted(must))
        g = g.loc[:, ~g.columns.duplicated(keep="last")]


        self.g_base = g
        # reset caches dependentes
        self.g_clipped = None
        self.color_map = None
        self.color_audit = None
        self.legend_dict = None
        return g

    def dissolve_by_attr(
        self,
        gdf: Optional[gpd.GeoDataFrame] = None,
        attr: str = "coarse_grp"
    ) -> gpd.GeoDataFrame:
        """
        Faz dissolve por 'attr' e em seguida explode em partes simples.
        Retorna uma linha por parte simples (com a coluna 'attr' preservada).
        """
        if gdf is None:
            gdf = self.g_clipped if self.g_clipped is not None else self.g_base
        if gdf is None:
            raise ValueError("Sem dados. Use combine_and_classify (e clip_to_bbox se quiser) antes.")

        gdf_diss = self.dissolve_by(gdf, attr=attr)

        # explode após dissolve (uma linha por parte simples)
        gdf_parts = self.explode_multipart(
            gdf_diss,
            repair=False,                 # já aplicamos buffer(0) no dissolve
            drop_empty=True,
            id_col=f"{attr}_part",        # identificador estável p/ cada parte
            keep_src_index=False
        )

        return gdf_parts




    def cull_small_parts_by_scale(
        self,
        gdf: Optional[gpd.GeoDataFrame] = None,
        *,
        min_area_mm2: float = 1.0,  # limiar = 1 mm² na escala da figura atual
        clean_after: bool = True,
        k_neighbors: int = 8        # nº de vizinhos candidatos por pequeno (KDTree)
    ) -> gpd.GeoDataFrame:
        """
        Versão rápida com cKDTree:
        - Calcula o limiar de área a partir da escala da figura (width_to_scale).
        - Usa _area_series para (i) áreas em m² e (ii) CRS métrico (projeta UMA vez).
        - Para cada polígono "pequeno", consulta k vizinhos "grandes" via KDTree
            (centros representativos). Prioriza quem intersecta/toca; senão, escolhe
            o mais próximo; desempate pelo MAIOR alvo.
        - Faz uma união por alvo (unary_union) e descarta os pequenos.
        Fallbacks: STRtree.nearest → sjoin → sjoin_nearest.
        """
        import numpy as np
        import pandas as pd
        from shapely.ops import unary_union

        # --- escala atual pela largura da figura ---
        N = self.width_to_scale(
            gdf=gdf,
            fig_width=self.fig_width,
            width_unit=self.width_unit,
            dpi=self.dpi,
            margin=self.margin,
            margin_unit=self.margin_unit,
            round_to=self.scale_round_to,
            round_mode=self.scale_round_mode,
            return_meta=False,
        )

        # ---------- entrada ----------
        if gdf is None:
            gdf = self.g_clipped if self.g_clipped is not None else self.g_base
        if gdf is None or gdf.empty:
            raise ValueError("Sem dados. Use combine_and_classify (e clip_to_bbox se quiser) antes.")
        orig_crs = gdf.crs

        g = gdf.loc[gdf.geometry.notna() & (~gdf.geometry.is_empty)].copy()
        if g.empty:
            return gdf

        # ---------- 1) limiar de área (m²) a partir de mm² no papel ----------
        area_thresh_m2 = (N * 1e-3) ** 2 * float(min_area_mm2)

        # ---------- 2) áreas e CRS métrico via _area_series ----------
        area_s, meta = self._area_series(g, verbose=False)
        strat = (meta or {}).get("strategy", "")
        crs_used = (meta or {}).get("crs_used", "")

        if strat.startswith("no_crs") or strat.startswith("fallback"):
            raise ValueError(
                "Não dá para podar por escala sem CRS métrico. "
                "Defina self.area_crs (ex.: Albers) ou atribua CRS projetado ao GeoDataFrame."
            )

        # ---------- 3) separar pequenos e grandes ----------
        small_idx = area_s.index[area_s < area_thresh_m2]
        if len(small_idx) == 0:
            return g  # nada a fazer
        big_idx = area_s.index[area_s >= area_thresh_m2]

        # projetar UMA vez para o CRS que _area_series usou
        gA = g.to_crs(crs_used) if (crs_used and (g.crs is None or str(g.crs) != str(crs_used))) else g.copy()

        big_area = area_s.loc[big_idx].to_dict()

        # ---------- 4) KDTree (rápido); fallbacks quando SciPy não estiver presente ----------
        to_union: dict[int, list[int]] = {}
        to_drop = set()

        def _finish_union_drop(gA_local):
            # executa uniões por alvo e dropa pequenos
            for tgt, sm_list in to_union.items():
                if not sm_list:
                    continue
                sm_union = unary_union(gA_local.loc[sm_list, "geometry"].tolist())
                gA_local.at[tgt, "geometry"] = unary_union([gA_local.at[tgt, "geometry"], sm_union])
            if to_drop:
                gA_local = gA_local.drop(index=list(to_drop))
            if clean_after:
                try:
                    gA_local["geometry"] = gA_local.geometry.buffer(0)
                except Exception:
                    pass
            g_out = gA_local.to_crs(orig_crs) if orig_crs else gA_local
            return gpd.GeoDataFrame(g_out, geometry="geometry", crs=orig_crs)

        # ---- 4A) Tenta SciPy KDTree em centroides/representative_point ----
        used_fast = False
        try:
            from scipy.spatial import cKDTree  # noqa: F401
            used_fast = True

            # arrays
            big_idx_arr = np.asarray(big_idx, dtype=object)
            small_idx_arr = np.asarray(small_idx, dtype=object)
            big_geoms = list(gA.loc[big_idx_arr, "geometry"].values)
            small_geoms = list(gA.loc[small_idx_arr, "geometry"].values)

            # pontos internos (representative_point é robusto p/ polígonos concavos)
            big_pts = np.array([[geom.representative_point().x, geom.representative_point().y] for geom in big_geoms], dtype="float64")
            small_pts = np.array([[geom.representative_point().x, geom.representative_point().y] for geom in small_geoms], dtype="float64")

            k = int(max(1, min(k_neighbors, len(big_pts))))
            tree = cKDTree(big_pts)

            dists, neigh = tree.query(small_pts, k=k, workers=-1)
            if k == 1:
                neigh = neigh.reshape(-1, 1)

            # mapa idx -> geom (para evitar pandas no loop)
            big_geom_by_idx = {int(i): g for i, g in zip(big_idx_arr, big_geoms)}

            # para cada pequeno, examina os k candidatos
            for row, s_idx in enumerate(small_idx_arr):
                s_idx = int(s_idx)
                s_geom = small_geoms[row]

                cand_big_rel = neigh[row]
                cand_big_abs = big_idx_arr[cand_big_rel]

                # 1) prioriza candidatos que INTERSECTAM/TOCAM; escolhe MAIOR área
                best_t, best_area, found_touch = None, -1.0, False
                for t_abs in cand_big_abs:
                    t_abs = int(t_abs)
                    if s_idx == t_abs:
                        continue
                    try:
                        if s_geom.intersects(big_geom_by_idx[t_abs]):
                            found_touch = True
                            a = float(big_area.get(t_abs, 0.0))
                            if a > best_area:
                                best_area, best_t = a, t_abs
                    except Exception:
                        continue
                # 2) se ninguém toca, escolhe o MAIS PRÓXIMO (desempate por área)
                if not found_touch:
                    best_t, best_area, best_d = None, -1.0, float("inf")
                    for t_abs in cand_big_abs:
                        t_abs = int(t_abs)
                        if s_idx == t_abs:
                            continue
                        try:
                            d = s_geom.distance(big_geom_by_idx[t_abs])
                        except Exception:
                            continue
                        a = float(big_area.get(t_abs, 0.0))
                        if (d < best_d) or (np.isclose(d, best_d) and a > best_area):
                            best_d, best_area, best_t = d, a, t_abs

                if best_t is not None:
                    to_union.setdefault(int(best_t), []).append(int(s_idx))
                    to_drop.add(int(s_idx))

        except Exception:
            used_fast = False  # sem SciPy? cai para STRtree/sjoin

        # ---- 4B) Fallback STRtree.nearest (você tem!) ----
        if (not used_fast) and (len(big_idx) > 0):
            try:
                from shapely.strtree import STRtree
                big_idx_arr = np.asarray(big_idx, dtype=object)
                small_idx_arr = np.asarray(small_idx, dtype=object)
                big_geoms = list(gA.loc[big_idx_arr, "geometry"].values)
                small_geoms = list(gA.loc[small_idx_arr, "geometry"].values)
                tree = STRtree(big_geoms)

                # 1) tenta pares que INTERSECTAM via query (compatível com sua build)
                try:
                    q = tree.query(small_geoms, predicate="intersects")
                except TypeError:
                    q = None

                if isinstance(q, tuple) and len(q) == 2 and len(q[0]) > 0:
                    left_i, right_j = q
                    si_abs = small_idx_arr[np.asarray(left_i)]
                    bi_abs = big_idx_arr[np.asarray(right_j)]
                    cand = pd.DataFrame({"src": si_abs, "tgt": bi_abs})
                    cand["tgt_area"] = cand["tgt"].map(big_area)
                    best = (cand.sort_values(["src", "tgt_area"], ascending=[True, False])
                                .drop_duplicates(subset=["src"], keep="first"))
                    for s_idx, t_idx in best[["src", "tgt"]].itertuples(index=False, name=None):
                        if int(s_idx) == int(t_idx):
                            continue
                        to_union.setdefault(int(t_idx), []).append(int(s_idx))
                        to_drop.add(int(s_idx))

                # 2) nearest por-geom para os restantes
                remain = [int(i) for i in small_idx if int(i) not in to_drop]
                if remain and hasattr(tree, "nearest"):
                    for s_idx in remain:
                        j = tree.nearest(gA.at[s_idx, "geometry"])
                        if j is None:
                            continue
                        t_idx = int(big_idx_arr[j])
                        if s_idx == t_idx:
                            continue
                        to_union.setdefault(t_idx, []).append(s_idx)
                        to_drop.add(s_idx)

            except Exception:
                pass

        # ---- 4C) Último fallback: sjoin / sjoin_nearest (cuidado com NaN → int) ----
        if len(big_idx) > 0:
            remain = [i for i in small_idx if i not in to_drop]
            if remain:
                try:
                    cand = gpd.sjoin(
                        gA.loc[remain, ["geometry"]],
                        gA.loc[big_idx, ["geometry"]],
                        how="left",
                        predicate="intersects",
                    ).reset_index().rename(columns={"index": "_sid"})
                    cand = cand.dropna(subset=["index_right"]).copy()
                    if not cand.empty:
                        cand["_sid"] = cand["_sid"].astype(int)
                        cand["index_right"] = cand["index_right"].astype(int)
                        cand["tgt_area"] = cand["index_right"].map(big_area)
                        best = (cand.sort_values(["_sid", "tgt_area"], ascending=[True, False])
                                    .drop_duplicates(subset=["_sid"], keep="first"))
                        for _, r in best.iterrows():
                            s_idx = int(r["_sid"]); t_idx = int(r["index_right"])
                            if s_idx == t_idx:
                                continue
                            to_union.setdefault(t_idx, []).append(s_idx)
                            to_drop.add(s_idx)
                except Exception:
                    pass

            remain = [i for i in small_idx if i not in to_drop]
            if remain:
                try:
                    near = gpd.sjoin_nearest(
                        gA.loc[remain, ["geometry"]],
                        gA.loc[big_idx, ["geometry"]],
                        how="left",
                        distance_col="__d__",
                    ).reset_index().rename(columns={"index": "_sid"})
                    near = near.dropna(subset=["index_right"]).copy()
                    if not near.empty:
                        near["_sid"] = near["_sid"].astype(int)
                        near["index_right"] = near["index_right"].astype(int)
                        near["tgt_area"] = near["index_right"].map(big_area)
                        bestn = (near.sort_values(["_sid", "tgt_area"], ascending=[True, False])
                                    .drop_duplicates(subset=["_sid"], keep="first"))
                        for _, r in bestn.iterrows():
                            s_idx = int(r["_sid"]); t_idx = int(r["index_right"])
                            if s_idx == t_idx:
                                continue
                            to_union.setdefault(t_idx, []).append(s_idx)
                            to_drop.add(s_idx)
                except Exception:
                    pass

        # ---- 5) caso extremo: TODO mundo é pequeno ----
        if not to_union and len(big_idx) == 0:
            tgt = int(area_s.idxmax())
            for s_idx in small_idx:
                s_idx = int(s_idx)
                if s_idx == tgt:
                    continue
                to_union.setdefault(tgt, []).append(s_idx)
                to_drop.add(s_idx)

        # ---- 6) aplicar uniões e finalizar ----
        return _finish_union_drop(gA)






    def width_to_scale(
        self,
        gdf: Optional[gpd.GeoDataFrame] = None,
        *,
        fig_width: float = 180.0,          # largura da figura
        width_unit: str = "mm",            # "mm" | "cm" | "in" | "px"
        dpi: Optional[int] = 300,          # usado se width_unit == "px"
        margin: float = 0.0,               # margem em cada lado (esquerda/direita)
        margin_unit: str = "mm",           # mesma convenção de unidades
        prefer_area_crs: bool = True,      # se False e CRS for geográfico, usa relação graus↔metros
        round_to: Optional[str] = "nice",  # None | "nice" (1,2,2.5,5 x 10^k)
        round_mode: str = "ceil",          # "ceil" (não corta mapa) | "nearest"
        return_meta: bool = False
    ) -> Union[int, Tuple[int, Dict[str, object]]]:
        """
        Calcula o denominador N da escala 1:N para caber a largura do gdf na largura útil da figura.
        Se prefer_area_crs=False e o CRS for geográfico, usa a aproximação fixa:
        1 m ≈ 1 / 108000 graus  (pois 1″ ≈ 30 m e 1° = 3600″).
        """


        # ---------- helpers ----------
        def _to_mm(value: float, unit: str) -> float:
            u = str(unit).lower()
            if u in ("mm", "millimeter", "millimeters"):
                return float(value)
            if u in ("cm", "centimeter", "centimeters"):
                return float(value) * 10.0
            if u in ("in", "inch", "inches"):
                return float(value) * 25.4
            if u in ("px", "pixel", "pixels"):
                if not dpi or dpi <= 0:
                    raise ValueError("Para width_unit='px', forneça um DPI positivo.")
                return float(value) * 25.4 / float(dpi)
            raise ValueError("Unidade inválida. Use: 'mm', 'cm', 'in', ou 'px'.")

        def _round_nice(n: float, mode: str = "ceil") -> int:
            if n <= 0:
                return 1
            k = math.floor(math.log10(n))
            steps = [1, 2, 2.5, 5, 10]
            if mode == "ceil":
                for s in steps:
                    val = s * (10 ** k)
                    if val >= n:
                        return int(math.ceil(val))
                return int(10 ** (k + 1))
            # nearest
            best_val, best_err = None, float("inf")
            for s in steps + [10]:
                val = s * (10 ** k)
                err = abs(val - n)
                if err < best_err:
                    best_err, best_val = err, val
            return int(round(best_val))

        # ---------- dados ----------
        if gdf is None:
            gdf = self.g_clipped if self.g_clipped is not None else self.g_base
        if gdf is None or gdf.empty:
            raise ValueError("Sem dados. Carregue um GeoDataFrame antes.")

        g = gdf.loc[gdf.geometry.notna() & (~gdf.geometry.is_empty)].copy()
        if g.empty:
            raise ValueError("Todas as geometrias estão nulas/vazias.")

        # largura de papel útil (mm → m)
        paper_w_mm = _to_mm(fig_width, width_unit)
        margin_mm = _to_mm(margin, margin_unit) if margin else 0.0
        content_w_mm = max(paper_w_mm - 2.0 * margin_mm, 1e-6)
        content_w_m = content_w_mm / 1000.0  # mm → m

        # ---------- mede a largura do dado ----------
        proj_used = None
        strategy = None

        # Se NÃO preferimos área_crs e o CRS é geográfico, usa relação graus↔metros (sem reprojetar)
        is_geographic = False
        try:
            is_geographic = bool(getattr(g.crs, "is_geographic", False))
        except Exception:
            is_geographic = False

        if (not prefer_area_crs) and is_geographic:
            # relação simples fornecida:
            DEGREE_PER_METER = 1.0 / (3600.0 * 30.0)  # ≈ 1/108000 ° por metro
            minx, miny, maxx, maxy = g.total_bounds
            width_deg = float(maxx - minx)
            # graus -> metros usando a relação fixa
            map_w_m = width_deg / DEGREE_PER_METER
            proj_used = "geographic (deg)"
            strategy = "geographic_constant_1deg≈108000m"
            # observação: esta aproximação ignora variação com latitude

        else:
            # caminho métrico (área_crs/LAEA/projetado nativo)
            try:
                if prefer_area_crs and getattr(self, "area_crs", None):
                    g = g.to_crs(self.area_crs)
                    proj_used = str(self.area_crs); strategy = "user_crs"
                elif g.crs is not None and getattr(g.crs, "is_projected", False):
                    proj_used = str(g.crs); strategy = "projected_native"
                else:
                    cen = g.geometry.unary_union.centroid
                    laea = (f"+proj=laea +lat_0={float(cen.y):.6f} +lon_0={float(cen.x):.6f} "
                            f"+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs")
                    g = g.to_crs(laea)
                    proj_used = laea; strategy = "auto_laea"
            except Exception as e:
                # Fallback: segue no CRS atual (medida pode não estar em metros)
                proj_used = str(g.crs)
                strategy = f"fallback:{type(e).__name__}"

            minx, miny, maxx, maxy = g.total_bounds
            map_w_m = float(maxx - minx)

        if not math.isfinite(map_w_m) or map_w_m <= 0:
            raise ValueError("Largura espacial inválida; verifique o CRS/geom.")

        # ---------- escala exata e arredondamento ----------
        scale_exact = map_w_m / content_w_m
        N = int(scale_exact)
        if round_to == "nice":
            N = _round_nice(scale_exact, mode=round_mode)

        meta = {
            "paper_width_mm": paper_w_mm,
            "margin_mm_each_side": margin_mm,
            "content_width_mm": content_w_mm,
            "map_width_m": float(map_w_m),
            "scale_exact": float(scale_exact),
            "scale_nice": int(N),
            "strategy": strategy,
            "proj_used": proj_used,
        }
        if (not prefer_area_crs) and is_geographic:
            meta.update({
                "width_deg": float(maxx - minx),
                "deg_per_meter_used": 1.0 / 108000.0,
                "note": "Conversão simples graus↔metros; ignora variação com latitude."
            })
        else:
            meta["note"] = "Escala calculada em CRS métrico; ajuste a altura da figura para evitar cortes."

        return (N, meta) if return_meta else N



    def _ensure_sigla_qml_loaded(self):
        """Carrega automaticamente QMLs padrão se o mapa de sigla ainda estiver vazio."""
        if getattr(self, "_qml_autoload_done", False):
            return
        self._qml_autoload_done = True
        if self.sigla_color_map:
            return
        try:
            paths = glob.glob("/mnt/data/*lito.qml")
            if paths:
                self.load_sigla_qml(paths, normalize_case=True)
        except Exception:
            # segue sem travar caso não exista /mnt/data
            pass

    def _clip_by_bbox(self, gdf: gpd.GeoDataFrame, bbox: Dict[str, float]):
        """
        Corta o GeoDataFrame pelos limites fornecidos em WGS84:
        bbox = {"min_lon":..., "max_lon":..., "min_lat":..., "max_lat":...}
        Retorna (gdf_clipped, meta_dict).
        """
        meta = {"strategy": "", "note": ""}
        if not bbox:
            meta.update({"strategy": "none"})
            return gdf, meta

        required = {"min_lon","max_lon","min_lat","max_lat"}
        if not required.issubset(bbox):
            meta.update({"strategy": "skipped", "note": "bbox incompleto"})
            return gdf, meta

        if gdf.crs is None:
            meta.update({"strategy": "skipped", "note": "gdf.crs ausente"})
            return gdf, meta

        # máscara em WGS84
        mask_wgs = gpd.GeoDataFrame(
            geometry=[box(bbox["min_lon"], bbox["min_lat"], bbox["max_lon"], bbox["max_lat"])],
            crs="EPSG:4326"
        )
        try:
            # reprojeta máscara para o CRS do dado (se necessário)
            mask = mask_wgs.to_crs(gdf.crs) if str(gdf.crs).upper() != "EPSG:4326" else mask_wgs
            g = gdf.copy()
            g["geometry"] = g.geometry.buffer(0)
            try:
                clipped = gpd.clip(g, mask)
                meta.update({"strategy": "gpd.clip"})
                return clipped, meta
            except Exception:
                # fallback robusto
                clipped = gpd.overlay(g, mask, how="intersection")
                meta.update({"strategy": "overlay(intersection)"})
                return clipped, meta
        except Exception as e:
            meta.update({"strategy": "failed", "note": f"{type(e).__name__}: {e}"})
            return gdf, meta


    def make_legend_json(self,
                        gdf: gpd.GeoDataFrame,
                        out_path: Union[str, Path],
                        group_attr: str = "coarse_grp",
                        sigla_col: Optional[str] = None,
                        nome_col: str = "NOME_UNIDA",
                        hier_col: str = "HIERARQUIA"):
        """
        Gera um JSON com, para cada `coarse_grp`, a lista dos itens usados no merge:
        { "<coarse_grp>": { "items": [ {"sigla": "...","nome": "...","hierarquia": "..."} , ... ] }, ... }
        - `sigla` é lida de `sigla_col` (se None: usa 'sigla' ou self.sigla_field, o que existir).
        - `nome` vem de NOME_UNIDA; `hierarquia` de HIERARQUIA (se houver).
        """
        out_path = Path(out_path)

        # decidir coluna de sigla
        c_sigla = (sigla_col or ("sigla" if "sigla" in gdf.columns else self.sigla_field))
        have_sigla = c_sigla in gdf.columns
        have_nome  = nome_col in gdf.columns
        have_hier  = hier_col in gdf.columns

        if not have_sigla and not have_nome and not have_hier:
            # nada a fazer
            data = {"note": "colunas de legenda ausentes no GeoDataFrame"}
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return out_path

        legend = {}
        cols = [c for c in [c_sigla, nome_col, hier_col] if c in gdf.columns]

        for grp, sub in gdf.groupby(group_attr, dropna=False):
            if grp in (None, "", "nan"):
                continue
            if sub.empty:
                continue
            df = sub[cols].copy()
            # normalizar para string
            for c in cols:
                df[c] = df[c].astype(object).where(pd.notna(df[c]), "").astype(str)
            df = df.drop_duplicates()
            items = []
            for _, r in df.iterrows():
                items.append({
                    "sigla": r.get(c_sigla, "") if have_sigla else "",
                    "nome":  r.get(nome_col, "") if have_nome  else "",
                    "hierarquia": r.get(hier_col, "") if have_hier else "",
                })
            if items:
                legend[str(grp)] = {"items": items}

        out_path.write_text(json.dumps(legend, ensure_ascii=False, indent=2), encoding="utf-8")
        return out_path


    # ---------- QML (SIGLA → #hex) ----------
    @staticmethod
    def _parse_qml_value_color_map(qml_path):
        """
        Lê QML categorizado e retorna (attr, {value -> #hex}) aceitando:
        - <prop k="color" v="r,g,b,a"> (QGIS "clássico")
        - <Option name="color" value="r,g,b,a, rgb:..."> (QGIS mais novo)
        """
        qml_path = Path(qml_path)
        if not qml_path.exists():
            return None, {}

        try:
            tree = ET.parse(qml_path)
        except Exception:
            return None, {}

        root = tree.getroot()
        rend = root.find(".//renderer-v2[@type='categorizedSymbol']")
        if rend is None:
            return None, {}

        # attr pode vir com aspas ou expressão (ex.: "SIGLA_UNID" ou concat(SIGLA_UNID))
        attr = rend.attrib.get("attr") or None
        if attr and len(attr) >= 2 and attr[0] == attr[-1] == '"':
            attr = attr[1:-1]  # tira aspas

        def _sym_fill_color(sym):
            # 1) <prop k="color" v="...">
            node = sym.find(".//prop[@k='color']")
            if node is not None and node.attrib.get("v"):
                hx = _rgba_to_hex(node.attrib["v"])
                if hx:
                    return hx
            # 2) <Option name="color" value="...">
            for opt in sym.findall(".//Option"):
                if opt.attrib.get("name") == "color" and opt.attrib.get("value"):
                    hx = _rgba_to_hex(opt.attrib["value"])
                    if hx:
                        return hx
            # 3) fallback: outline/line color
            for k in ("outline_color", "line_color"):
                node = sym.find(f".//prop[@k='{k}']")
                if node is not None and node.attrib.get("v"):
                    hx = _rgba_to_hex(node.attrib["v"])
                    if hx:
                        return hx
                for opt in sym.findall(".//Option"):
                    if opt.attrib.get("name") == k and opt.attrib.get("value"):
                        hx = _rgba_to_hex(opt.attrib["value"])
                        if hx:
                            return hx
            return ""

        # símbolos -> cor
        sym_color = {}
        for sym in root.findall(".//symbols/symbol"):
            name = sym.attrib.get("name", "")
            sym_color[name] = _sym_fill_color(sym)

        # categorias -> pega a cor do símbolo correspondente
        mapping = {}
        cats = rend.find("categories")
        if cats is not None:
            for cat in cats.findall("category"):
                value = (cat.attrib.get("value") or "").strip()
                if not value:
                    continue
                symname = cat.attrib.get("symbol")
                c = sym_color.get(symname, "")
                if not c:
                    # alguns QML trazem cor na própria <category>
                    cattr = cat.attrib.get("color") or cat.attrib.get("symbol_color")
                    if cattr:
                        c = _rgba_to_hex(cattr)
                if c:
                    mapping[value] = c

        return attr, mapping




    def load_sigla_qml(self, qml_paths, normalize_case=True):
        paths = qml_paths if isinstance(qml_paths, (list, tuple)) else [qml_paths]
        for p in paths:
            attr, mp = self._parse_qml_value_color_map(p)
            if not mp:
                continue

            # normalização opcional (duplica chaves upper/lower)
            if normalize_case:
                norm_mp = {}
                for k, v in mp.items():
                    norm_mp[k] = v
                    norm_mp[str(k).upper()] = v
                    norm_mp[str(k).lower()] = v
                mp = norm_mp

            # guarda a paleta (para auditoria/metadata)
            self.qml_palettes.append({"attr": attr, "map": mp, "n": len(mp)})

            # **sempre** alimente o mapa de SIGLA -> cor (retrocompat + funciona mesmo se attr vier como concat(...))
            for k, v in mp.items():
                self.sigla_color_map[k] = v



    def _best_qml_mix_for_group(self, gdf, grp, area_s):
        # garante cores de SIGLA a partir dos QMLs padrão
        self._ensure_sigla_qml_loaded()


        # garante pesos (se vier None)
        if area_s is None:
            area_s = pd.Series(1.0, index=gdf.index)

        # 1) candidatas por configuração (se houver)
        candidates_cfg = getattr(self, "qml_attr_candidates", None)
        candidates = []
        if candidates_cfg:
            if isinstance(candidates_cfg, (list, tuple, set)):
                candidates = list(candidates_cfg)
            else:
                candidates = [candidates_cfg]

        # 2) auto-detecção (se nada configurado)
        if not candidates:
            lower = [str(c).lower() for c in gdf.columns]
            for col in gdf.columns:
                l = str(col).lower()
                if l == "sigla" or l.startswith("sigla_"):
                    candidates.append(col)

            # põe 'sigla' (exata) no topo, se existir
            candidates = sorted(set(candidates), key=lambda c: (0 if str(c).lower() == "sigla" else 1, str(c)))

        # 3) mantém só colunas existentes (ordem preservada)
        seen = set()
        candidates = [c for c in candidates if (c in gdf.columns) and not (c in seen or seen.add(c))]

        best_attr, best_pack, best_area = None, None, 0.0

        for col in candidates:
            pack = self._weighted_items_for_column(
                gdf, grp, col,
                color_getter=lambda v: self._lookup_sigla_color(v),
                area_s=area_s
            )
            area = float(pack.get("total_area") or 0.0)
            mix  = pack.get("mix") or ""
            # precisa ter alguma cobertura real (mix não vazio) e área maior que a atual
            if area > best_area and mix:
                best_attr, best_pack, best_area = col, pack, area

        # 4) fallback: tenta 'sigla' em variantes comuns, se nada deu certo
        if best_pack is None:
            for col in ("sigla", "SIGLA", "Sigla"):
                if col in gdf.columns:
                    pack = self._weighted_items_for_column(
                        gdf, grp, col,
                        color_getter=lambda v: self._lookup_sigla_color(v),
                        area_s=area_s
                    )
                    best_attr, best_pack, best_area = col, pack, float(pack.get("total_area") or 0.0)
                    break

        if best_pack:
            return (
                best_pack.get("mix", ""),
                best_attr,
                best_pack.get("items", []),
                best_area
            )

        # nada encontrado
        return "", None, [], 0.0




    def _lookup_sigla_color(self, sigla: str) -> str:
        if not self.sigla_color_map:
            return ""
        if sigla in self.sigla_color_map:
            return self.sigla_color_map[sigla]
        s_up = str(sigla).upper()
        if s_up in self.sigla_color_map:
            return self.sigla_color_map[s_up]
        s_lo = str(sigla).lower()
        return self.sigla_color_map.get(s_lo, "")

    # ---------- Merging ----------
    @staticmethod
    def _to_crs_safe(gdf: gpd.GeoDataFrame, crs):
        try:
            if gdf.crs and crs and gdf.crs != crs:
                return gdf.to_crs(crs)
        except Exception:
            pass
        return gdf

    def merge(self, gdfs: List[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
        """Mescla GeoDataFrames alinhando o CRS (usa o do primeiro com CRS válido)."""
        target_crs = None
        for g in gdfs:
            if hasattr(g, "crs") and g.crs:
                target_crs = g.crs
                break
        g_aligned = [self._to_crs_safe(g, target_crs) for g in gdfs]
        merged = gpd.GeoDataFrame(pd.concat(g_aligned, ignore_index=True), crs=target_crs)
        return merged

    # ---------- Classificação ----------
    @staticmethod
    def _norm_txt(x):
        if x is None: return None
        s = str(x).strip()
        if not s: return None
        s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
        return s.upper()

    @staticmethod
    def _choose_minmax(vmin, vmax, domain):
        def norm(x):
            if x is None: return None
            s = str(x).strip()
            if not s: return None
            s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
            s = s.upper()
            return s if s in domain else None
        m = norm(vmin); M = norm(vmax)
        if m is None and M is None: return (None, None)
        if m is None: m = M
        if M is None: M = m
        return (m, M)

    def _extract_idade_code(self, sigla):
        """
        Extrai o prefixo de idade (ex.: 'P3T1', 'NP3C_CORTADO_', 'C_CORTADO_').
        Regras especiais:
        - Se o prefixo terminar em 'C_' e imediatamente após vier 'cortado_' (qualquer caixa),
            mantemos isso no idade_code como 'C_CORTADO_' (em MAIÚSCULAS).
        """
        s = "" if sigla is None else str(sigla)
        m = re.match(r"^([A-Z0-9_]+)", s)   # parte inicial em maiúsculas/dígitos/_ (ex.: 'NP3C_')
        base = m.group(1) if m else ""

        # inclui 'C_CORTADO_' se aparecer logo em seguida (interface Cambriano 'Є')
        rest = s[len(base):]
        if base.endswith("C_") and rest.lower().startswith("cortado_"):
            base = base + "CORTADO_"  # vira ... 'C_CORTADO_'

        return base

    def _tokenize_rest(self, sigla, idade_code):
        """
        Retorna tokens após o idade_code. Agora o idade_code pode conter 'C_CORTADO_'.
        """
        rest = str(sigla)[len(idade_code):].lstrip("_")
        if not rest:
            return []
        return [t for t in re.split(r"_+", rest) if t]

    def _norm_greek(self, tok):
        GREEK = {"ALFA":"alfa","ALPHA":"alfa","BETA":"beta","GAMMA":"gamma","GAMA":"gamma",
                 "DELTA":"delta","LAMBDA":"lambda","MU":"mu"}
        t = re.sub(r"[^A-Za-z]+","", tok).upper()
        return GREEK.get(t)

    def _letters_stem(self, tok):
        tok = tok.strip("_")
        tok = re.sub(r"^[0-9]+","", tok)
        m = re.match(r"^([a-z]+)", tok)
        return (m.group(1)[:self.stem_len] if m else None)

    def _parse_sigla(self, s):
        s = str(s) if s is not None else ""
        idade = self._extract_idade_code(s)
        toks = self._tokenize_rest(s, idade)
        greek = stem = None
        for t in toks:
            if re.fullmatch(r"[A-Z0-9]+", t) and t in self.ignore_allcaps:
                continue
            if greek is None:
                g = self._norm_greek(t)
                if g: greek = g; continue
            if stem is None:
                st = self._letters_stem(t)
                if st: stem = st
            if greek and stem: break
        if   greek: coarse = f"{idade}|{greek}"
        elif stem : coarse = f"{idade}|{stem}"
        else     : coarse = idade or ""
        return idade, (greek or ""), (stem or ""), coarse

    @staticmethod
    def _macro_from_idade_code_simple(idade_code):
        """
        Macro-era derivada apenas do idade_code (heurística simples).
        Dá prioridade ao Cambriano 'C_CORTADO' onde quer que apareça.
        """
        ic = str(idade_code).upper()

        # prioridade: Cambriano (Є) detectado explicitamente
        if "C_CORTADO" in ic:
            return "Paleozóico"

        # blocos padrão
        if re.match(r"^(A|PP|MP|NP)", ic):
            return "Pre-cambriano"
        if re.match(r"^(J|K|T|JK)", ic):
            return "Mesozóico"
        if re.match(r"^(Q|N|PG|PL|PE|E)", ic):
            return "Cenozóico"
        if re.match(r"^(P(?!P)|D|C(?!C)|S|O|CM)", ic):
            return "Paleozóico"

        return ""

    def _norm_nome_value(self, s: str) -> str:
        """Normaliza NOME_UNIDA p/ comparação (maiúscula, sem acento, espaços comprimidos)."""
        if s is None:
            return ""
        t = str(s).strip()
        if not t:
            return ""
        t = ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')
        t = re.sub(r"\s+", " ", t)
        return t.upper()

    # >>> ADICIONE NA CLASSE (escolhe o rótulo canônico por contagem ou área)
    def _pick_canonical_grp(self, sub_gdf, area_s=None):
        """
        sub_gdf: subconjunto de linhas de um mesmo nome (já filtrado)
        area_s: Series de áreas (mesmo índice do gdf) ou None -> usa contagem
        Retorna o rótulo 'coarse_grp' dominante (empate: ordem alfabética).
        """
        if sub_gdf.empty or "coarse_grp" not in sub_gdf.columns:
            return None

        # remove vazios
        vals = pd.unique(sub_gdf["coarse_grp"].astype("string"))
        grps = [v for v in vals if v and str(v).lower() != "nan"]
        if not grps:
            return None
        if len(grps) == 1:
            return grps[0]

        if area_s is not None:
            try:
                weights = sub_gdf.groupby("coarse_grp").apply(lambda d: float(area_s.loc[d.index].sum()))
            except Exception:
                weights = sub_gdf.groupby("coarse_grp").size().astype(float)
        else:
            weights = sub_gdf.groupby("coarse_grp").size().astype(float)

        if "" in weights.index:
            weights = weights.drop("", errors="ignore")
        if weights.empty:
            return None

        maxw = weights.max()
        # desempate determinístico por ordem alfabética
        dominant = weights[weights == maxw].sort_index().index[0]
        return dominant



    def classify(self, gdf: gpd.GeoDataFrame, enforce_mode="flag",
                fields_to_keep: Optional[List[str]]=None,
                # fusão por nome (igual à sua versão)
                name_merge: bool = True,
                name_merge_area_weighted: bool = False,
                # NOVO: colapsar todo o Cenozóico em um grupo
                collapse_cenozoic: bool = False,
                collapse_cenozoic_label: str = "CENOZOICO") -> gpd.GeoDataFrame:
        """
        Adiciona idade_code/greek/stem/coarse_grp + dominó + (opcionais)
        fusão por NOME_UNIDA e colapso do Cenozóico em um grupo único.
        Mantém a coluna original (ex.: SIGLA_UNID) e cria 'sigla'.
        """

        g = gdf.copy()
        if self.sigla_field not in g.columns:
            raise KeyError(f"Coluna '{self.sigla_field}' não encontrada. Colunas: {list(g.columns)}")

        # cria 'sigla' sem renomear a original
        g["sigla"] = g[self.sigla_field].astype(str)

        parsed = g["sigla"].apply(lambda s: pd.Series(self._parse_sigla(s),
                                                    index=["idade_code","greek","stem","coarse_grp"]))
        g = pd.concat([g, parsed], axis=1)
        g["sigla_era"] = g["idade_code"].apply(self._macro_from_idade_code_simple)

        # ---------- dominó ----------
        EON = {"ARQUEANO","PROTEROZOICO","FANEROZOICO"}
        ERA = {"PALEOZOICO","MESOZOICO","CENOZOICO"}
        eon_pairs = g[[self.eon_min_field, self.eon_max_field]].apply(
            lambda r: self._choose_minmax(r[self.eon_min_field], r[self.eon_max_field], EON), axis=1)
        eon_m = [p[0] for p in eon_pairs]; eon_M = [p[1] for p in eon_pairs]

        eon_dom = []
        for a,b in zip(eon_m, eon_M):
            if a is None and b is None: eon_dom.append("Fanerozóico")
            elif a in {"ARQUEANO","PROTEROZOICO"} and b in {"ARQUEANO","PROTEROZOICO"}: eon_dom.append("Pre-cambriano")
            elif a in {"ARQUEANO","PROTEROZOICO"} and b == "FANEROZOICO": eon_dom.append("Pre-cambriano|Paleozóico")
            elif a == "FANEROZOICO" and b == "FANEROZOICO": eon_dom.append("Fanerozóico")
            else: eon_dom.append("Fanerozóico" if (a=="FANEROZOICO" or b=="FANEROZOICO") else "Pre-cambriano")

        era_pairs = g[[self.era_min_field, self.era_max_field]].apply(
            lambda r: self._choose_minmax(r[self.era_min_field], r[self.era_max_field], ERA), axis=1)
        era_m = [p[0] for p in era_pairs]; era_M = [p[1] for p in eon_pairs]  # <- cuidado: era_pairs
        era_m = [p[0] for p in era_pairs]; era_M = [p[1] for p in era_pairs]  # (corrige a linha acima, se existir)

        def era_label(m, M):
            if m is None and M is None: return ""
            if m == "PALEOZOICO" and M == "PALEOZOICO": return "Paleozóico"
            if m == "PALEOZOICO" and M == "MESOZOICO": return "Paleozóico|Mesozóico"
            if m == "MESOZOICO" and M == "MESOZOICO": return "Mesozóico"
            if m == "MESOZOICO" and M == "CENOZOICO": return "Mesozóico|Cenozóico"
            if m == "CENOZOICO" and M == "CENOZOICO": return "Cenozóico"
            if m == M:
                return {"PALEOZOICO":"Paleozóico","MESOZOICO":"Mesozóico","CENOZOICO":"Cenozóico"}.get(m, "")
            return ""
        era_stage = [era_label(m, M) for m, M in zip(era_m, era_M)]

        def has_np(ic): return "NP" in ic
        def has_perm(ic):
            return bool(re.search(r"(^|[^A-Z])P(?![A-Z])", ic) or re.search(r"(^|[^A-Z])P[0-9]", ic))
        def has_k(ic):  return ("K" in ic) or ("JK" in ic)

        macro = []
        for ed, er, ic in zip(eon_dom, era_stage, g["idade_code"].astype(str).str.upper()):
            if ed == "Pre-cambriano":
                macro.append("Pre-cambriano")
            elif ed == "Pre-cambriano|Paleozóico":
                macro.append("Pre-cambriano" if has_np(ic) else "Paleozóico")
            elif ed == "Fanerozóico":
                if er in ("Paleozóico","Mesozóico","Cenozóico",""):
                    macro.append(er)
                elif er == "Paleozóico|Mesozóico":
                    macro.append("Paleozóico" if has_perm(ic) else "Mesozóico")
                elif er == "Mesozóico|Cenozóico":
                    macro.append("Mesozóico" if has_k(ic) else "Cenozóico")
                else:
                    macro.append("")
            else:
                macro.append("")
        g["eon_domino"] = eon_dom
        g["macro_era"]  = macro

        def allowed_set(macro_era):
            return {macro_era} if macro_era in {"Pre-cambriano","Paleozóico","Mesozóico","Cenozóico"} \
                else {"Pre-cambriano","Paleozóico","Mesozóico","Cenozóico"}
        g["domino_ok"] = [
            1 if (se := self._macro_from_idade_code_simple(ic)) in allowed_set(me) or se == "" else 0
            for ic, me in zip(g["idade_code"].astype(str), g["macro_era"])
        ]
        if enforce_mode == "mask":
            g.loc[g["domino_ok"] == 0, "coarse_grp"] = ""

        # ---------- Fusão por NOME_UNIDA (puxa para o grupo MAIOR) ----------
        if name_merge and (self.name_field in g.columns):
            g["_nome_norm_"] = g[self.name_field].apply(self._norm_nome_value)

            area_s = None
            if name_merge_area_weighted:
                try:
                    area_s, _meta = self._area_series(g)
                except Exception:
                    area_s = None

            # pesos globais por grupo
            if name_merge_area_weighted and area_s is not None:
                _gw = g.groupby("coarse_grp").apply(lambda d: float(area_s.loc[d.index].sum()))
            else:
                _gw = g.groupby("coarse_grp").size().astype(float)
            global_w = _gw.to_dict()

            for nome, sub in g.groupby("_nome_norm_"):
                if not nome or sub.empty:
                    continue
                grps = pd.unique(sub["coarse_grp"].astype("string"))
                grps = [x for x in grps if x not in (None, "", "nan")]
                if len(grps) <= 1:
                    continue
                if not (sub["domino_ok"] == 1).all():
                    continue
                macros = [m for m in pd.unique(sub["macro_era"].astype("string")) if m not in ("", "nan", None)]
                if len(macros) > 1:
                    continue

                if name_merge_area_weighted and area_s is not None:
                    _lw = sub.groupby("coarse_grp").apply(lambda d: float(area_s.loc[d.index].sum()))
                else:
                    _lw = sub.groupby("coarse_grp").size().astype(float)
                local_w = _lw.to_dict()

                def _key(gname):
                    return (-float(global_w.get(gname, 0.0)),
                            (-float(local_w.get(gname, 0.0))),
                            str(gname))
                canon = sorted(grps, key=_key)[0]
                g.loc[sub.index, "coarse_grp"] = canon

            g = g.drop(columns=["_nome_norm_"], errors="ignore")

        # ---------- NOVO: Colapso do Cenozóico ----------
        if collapse_cenozoic:
            mask_cz = (g["macro_era"].astype(str) == "Cenozóico") & \
                    (g["domino_ok"] == 1) & \
                    (g["coarse_grp"].astype(str) != "")
            # um único rótulo para todo Cenozóico
            g.loc[mask_cz, "coarse_grp"] = collapse_cenozoic_label

        # ---------- seleção de campos ----------
        keep = (fields_to_keep or []) + [
            self.sigla_field, "sigla",
            "idade_code","greek","stem","coarse_grp","sigla_era",
            "eon_domino","macro_era","domino_ok","geometry"
        ]
        keep = [c for c in keep if (c in g.columns or c == "geometry")]
        g = g[keep].copy()
        for c in g.columns:
            if c != "geometry":
                g[c] = g[c].astype(object).where(pd.notna(g[c]), "")
        return g








    # ---------- QML ----------
    @staticmethod
    def _flatten_idade_code_map(idade_code_map_rich: Dict[str, Dict]) -> Dict[str,str]:
        """
        {"era":{"nome":{"code","color"}}} -> {"PP":"#..", "K":"#..", "C_CORTADO":"#..", ...}
        """
        flat = {}
        for sub in (idade_code_map_rich or {}).values():
            for cfg in (sub or {}).values():
                code = str(cfg.get("code","")).upper()
                color = cfg.get("color","")
                if not code: continue
                if code.lower() == "c_cortado" or code == "C_CORTADO":
                    code = "C_CORTADO"
                flat[code] = color
        return flat

    @staticmethod
    def _get_idade_code_from_grp(grp):
        if grp is None: return ""
        s = str(grp).split("|", 1)[0]
        m = re.match(r"^([A-Z0-9_]+)", s)
        return (m.group(1) if m else s).upper()

    @staticmethod
    def _collect_unique_for_group(gdf, grp, col):
        if col not in gdf.columns: return []
        vals = gdf.loc[gdf["coarse_grp"]==grp, col]
        if vals.empty: return []
        uniq = []
        for v in pd.unique(vals.astype("string")):
            if v and str(v).lower() != "nan":
                uniq.append(str(v))
        return uniq

    def _find_idade_color(self, idade_code, flat_map):
        code = str(idade_code).upper()
        tokens = sorted(flat_map.keys(), key=len, reverse=True)
        for t in tokens:
            if t == "P":
                if re.search(r"(^|[^A-Z])P(?![A-Z])", code) or re.search(r"(^|[^A-Z])P[0-9]", code):
                    return flat_map[t]
            elif t == "C_CORTADO":
                if "C_CORTADO" in code:
                    return flat_map[t]
            else:
                if code.startswith(t) or t in code:
                    return flat_map[t]
        for t in ("NP","MP","PP","A","K","J","T","Q","N","PG","D","S","O","C"):
            if t in flat_map and (code.startswith(t) or t in code):
                return flat_map[t]
        return ""

    # 3) DENTRO DA CLASSE, adicione estes dois métodos auxiliares:

    def _area_series(self, gdf: gpd.GeoDataFrame, verbose: bool = False):
        """
        Retorna (areas_m2: Series, meta: dict).
        Estratégia:
        - se self.area_crs: usa esse CRS (supõe equal-area);
        - se gdf.crs projetado: usa direto;
        - se gdf.crs geográfico: cria LAEA centrado no dado e projeta;
        - se gdf.crs None: não arrisco reprojetar -> pesos = 1.0 e aviso.
        """
        meta = {"strategy": "", "crs_used": "", "note": ""}
        try:
            if self.area_crs:
                g_tmp = gdf.to_crs(self.area_crs)
                meta.update({"strategy": "user_crs", "crs_used": str(self.area_crs)})
                return g_tmp.geometry.area, meta

            if gdf.crs:
                if getattr(gdf.crs, "is_projected", False):
                    meta.update({"strategy": "projected_native", "crs_used": str(gdf.crs)})
                    return gdf.geometry.area, meta
                # geográfico -> LAEA centrado
                # pega o centroide em graus
                cen = gdf.geometry.unary_union.centroid
                lon0, lat0 = float(cen.x), float(cen.y)
                laea = f"+proj=laea +lat_0={lat0:.6f} +lon_0={lon0:.6f} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
                g_tmp = gdf.to_crs(laea)
                meta.update({"strategy": "auto_laea", "crs_used": laea})
                return g_tmp.geometry.area, meta

            # sem CRS: não dá para reprojetar com segurança
            meta.update({"strategy": "no_crs_equal_weights", "note": "gdf.crs ausente; pesos=1.0"})
            return pd.Series(1.0, index=gdf.index), meta

        except Exception as e:
            # fallback seguro: pesos = 1.0
            meta.update({"strategy": "fallback_equal_weights", "note": f"{type(e).__name__}: {e}"})
            return pd.Series(1.0, index=gdf.index), meta

    def _weighted_items_for_column(self, gdf: gpd.GeoDataFrame, grp: str, col: str,
                                color_getter, area_s: pd.Series):
        """
        Retorna dict com:
        {
            "items": [{"value":..., "area":..., "weight":..., "color":...}, ...],
            "mix": "#RRGGBB",
            "total_area": float
        }
        """
        out = {"items": [], "mix": "", "total_area": 0.0}
        if col not in gdf.columns:
            return out
        mask = (gdf["coarse_grp"] == grp)
        if not mask.any():
            return out
        sub = gdf.loc[mask]
        areas = area_s.loc[sub.index].fillna(0.0)

        # soma área por valor
        area_by_val = sub.groupby(col).apply(lambda df: areas.loc[df.index].sum()).to_dict()
        total = float(sum(a for a in area_by_val.values() if a > 0))
        out["total_area"] = total

        # monta lista e cores
        colors, weights = [], []
        for v, a in area_by_val.items():
            if a <= 0:
                continue
            c = color_getter(v)
            item = {"value": None if pd.isna(v) else str(v), "area": float(a), "weight": 0.0, "color": c or ""}
            if total > 0:
                item["weight"] = float(a/total)
            out["items"].append(item)
            if c:
                colors.append(c); weights.append(a)

        out["mix"] = mix_weighted(colors, weights) if weights else ""
        return out


    def _mix_weighted_from_column(self, gdf: gpd.GeoDataFrame, grp: str, col: str,
                                color_getter, area_s: pd.Series) -> str:
        """
        Para um 'coarse_grp', calcula a cor média ponderada pela área a partir da coluna 'col'.
        - color_getter(v) deve devolver '#RRGGBB' para o valor v (ou '' se não houver).
        """
        if col not in gdf.columns:
            return ""
        mask = (gdf["coarse_grp"] == grp)
        if not mask.any():
            return ""
        sub = gdf.loc[mask]
        areas = area_s.loc[sub.index]
        if areas.isna().all():
            return ""

        # soma a área por categoria
        weights_by_val = {}
        for val, idxs in sub.groupby(col).groups.items():
            w = areas.loc[idxs].sum()
            if w > 0:
                weights_by_val[val] = float(w)

        if not weights_by_val:
            return ""

        colors, weights = [], []
        for v, w in weights_by_val.items():
            c = color_getter(v)
            if c:
                colors.append(c); weights.append(w)

        return mix_weighted(colors, weights)


    # 4) SUBSTITUA O MÉTODO _build_color_map_for_coarse por esta versão:

    def _build_color_map_for_coarse(self, gdf: gpd.GeoDataFrame, coarse_grps: List[str]):
        # garante que os QMLs tenham sido lidos (preenche sigla_color_map)
        self._ensure_sigla_qml_loaded()

        flat_age = self._flatten_idade_code_map(self.idade_code_map_rich)
        roc_colors_norm = { _norm_key(k): v for k,v in (self.classe_roc_colors or {}).items() }
        r1_colors_norm  = { _norm_key(k): v for k,v in (self.classe_r1_colors  or {}).items() }


        # áreas e metadados
        if self.area_weighting:
            area_s, area_meta = self._area_series(gdf, verbose=True)
        else:
            area_s, area_meta = pd.Series(1.0, index=gdf.index), {"strategy":"equal_weights"}

        cmap = {
            grp: {"grp_color":"", "idade_color":"", "roc_colors":"", "r1_colors":"", "qml_colors":"", "qml_attr":None}
            for grp in coarse_grps
        }

        audit = {
            "area": area_meta,
            "groups": {}
        }

        # derivados e auditoria
        for grp in coarse_grps:
            audit["groups"][grp] = {}

            # idade (não pondera)
            idade_code  = self._get_idade_code_from_grp(grp)
            idade_color = self._find_idade_color(idade_code, flat_age)
            cmap[grp]["idade_color"] = idade_color
            audit["groups"][grp]["idade_code"]  = idade_code
            audit["groups"][grp]["idade_color"] = idade_color

            # QML: escolher automaticamente a palette (attr) que mais cobre a área do grupo
            qml_mix, qml_attr, qml_items, qml_area = self._best_qml_mix_for_group(gdf, grp, area_s)
            cmap[grp]["qml_colors"] = qml_mix
            cmap[grp]["qml_attr"]   = qml_attr
            audit["groups"][grp]["sigla_qml"] = {
                "attr_used": qml_attr or "",
                "items": qml_items,
                "mix": qml_mix or "",
                "total_area": float(qml_area or 0.0),
            }

            # ROC ponderado
            roc_pack = self._weighted_items_for_column(
                gdf, grp, "CLASSE_ROC",
                color_getter=lambda v: roc_colors_norm.get(_norm_key(v), ""),
                area_s=area_s
            )
            cmap[grp]["roc_colors"] = roc_pack["mix"]
            audit["groups"][grp]["roc"] = roc_pack

            # R1 ponderado
            r1_pack = self._weighted_items_for_column(
                gdf, grp, "CLASSE_R_1",
                color_getter=lambda v: r1_colors_norm.get(_norm_key(v), ""),
                area_s=area_s
            )
            cmap[grp]["r1_colors"] = r1_pack["mix"]
            audit["groups"][grp]["r1"] = r1_pack


        # escolha preliminar — prioridade: QML → ROC (classe única) → idade
        for grp in coarse_grps:
            if cmap[grp]["qml_colors"]:
                prelim = cmap[grp]["qml_colors"]
            else:
                roc_vals = self._collect_unique_for_group(gdf, grp, "CLASSE_ROC")
                if len(roc_vals) == 1 and _norm_key(roc_vals[0]) in roc_colors_norm:
                    prelim = roc_colors_norm[_norm_key(roc_vals[0])]
                else:
                    prelim = cmap[grp]["idade_color"] or "#DDDDDD"
            cmap[grp]["grp_color"] = prelim
            audit["groups"][grp]["prelim_grp_color"] = prelim

        # funções de duplicatas e snapshots para auditoria
        def dup_sets():
            inv = {}
            for gk, d in cmap.items():
                c = d["grp_color"]
                inv.setdefault(c, []).append(gk)
            return [v for v in inv.values() if len(v) > 1]

        # Rodada A — idade + ROC
        for group_list in dup_sets():
            for grp in group_list:
                roc = cmap[grp]["roc_colors"]; ida = cmap[grp]["idade_color"]
                if roc:
                    base = ida or roc
                    cmap[grp]["grp_color"] = mix_two(base, roc, 0.5)
        # snapshot A
        for grp in coarse_grps:
            audit["groups"][grp]["after_A"] = cmap[grp]["grp_color"]

        # Rodada B — +R1
        for group_list in dup_sets():
            for grp in group_list:
                roc = cmap[grp]["roc_colors"]; r1 = cmap[grp]["r1_colors"]; ida = cmap[grp]["idade_color"]
                base = mix_two(ida, roc, 0.5) if roc else (ida or r1 or "#DDDDDD")
                cmap[grp]["grp_color"] = mix_two(base, r1, 0.5) if r1 else base
        # snapshot B
        for grp in coarse_grps:
            audit["groups"][grp]["after_B"] = cmap[grp]["grp_color"]

        # Rodada C — jitter (mais sutil)
        tries = 0
        dups = dup_sets()
        while dups and tries < 3:
            for group_list in dups:
                for i, grp in enumerate(sorted(group_list)):
                    base = cmap[grp]["grp_color"] or "#DDDDDD"
                    # antes: dh=0.10, dl=0.06/0.04 —> agora mais suave:
                    cmap[grp]["grp_color"] = jitter(base, i, dh=0.05, dl=0.02 if tries>0 else 0.015)
            tries += 1
            dups = dup_sets()
        # snapshot final
        for grp in coarse_grps:
            audit["groups"][grp]["final_grp_color"] = cmap[grp]["grp_color"]

        return cmap, audit

    @staticmethod
    def _dedup_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Remove colunas com nomes duplicados, mantendo a última ocorrência."""
        return gdf.loc[:, ~gdf.columns.duplicated(keep="last")]



    def make_qml(self, gdf: gpd.GeoDataFrame, qml_path: Union[str, Path], attr="coarse_grp",
                audit_json_path: Optional[Union[str, Path]] = None,
                source_gdf_for_mix: Optional[gpd.GeoDataFrame] = None):
        """Gera um .qml categorizado e um JSON de auditoria (cores/áreas/mixes/rodadas).
        - gdf: camada alvo do QML (idealmente pós-dissolve)
        - source_gdf_for_mix: camada base para calcular mixes (idealmente pré-dissolve)
        """

        # 0) blindagem contra colunas duplicadas
        gdf = self._dedup_columns(gdf)
        if source_gdf_for_mix is not None:
            base_gdf = self._dedup_columns(source_gdf_for_mix)
        else:
            base_gdf = gdf

        # 1) checagem do atributo (garante 1-D)
        if attr not in gdf.columns:
            raise KeyError(f"Coluna '{attr}' não encontrada para o QML. Colunas: {list(gdf.columns)}")

        col = gdf[attr]
        if getattr(col, "ndim", 1) != 1:
            # se ainda assim vier DataFrame (improvável após dedup), pega a última coluna
            col = col.iloc[:, -1]

        # 2) valores (categorias) válidos
        values = sorted(v for v in pd.unique(col.astype("string")) if v not in (None, "", "nan"))

        # 3) construir cores
        if attr == "coarse_grp":
            # usa base_gdf (pré-dissolve) para calcular as misturas; QML é aplicado na camada 'gdf'
            cmap, audit = self._build_color_map_for_coarse(base_gdf, values)
            cats = [{"value": v, "label": v, "color": (cmap[v]["grp_color"] or "#DDDDDD")} for v in values]
        else:
            # fallback simples
            cmap, audit = None, {"note": "audit only available for attr='coarse_grp'"}
            cats = []
            for i, v in enumerate(values):
                c = jitter("#88AAFF", i, dh=0.25, dl=0.15)
                cats.append({"value": v, "label": v, "color": c})

        qml_xml = _qml_categorized(attr, cats, outline_rgb=_hex_to_rgba(self.outline_color), outline_w=self.outline_width)
        qml_path = Path(qml_path)
        qml_path.write_text(qml_xml, encoding="utf-8")

        audit_path = None
        if audit is not None:
            audit_path = Path(audit_json_path) if audit_json_path else qml_path.with_suffix(".audit.json")
            audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

        return qml_path, audit_path




    # ---------- IO ----------
    @staticmethod
    def save_shp(gdf: gpd.GeoDataFrame, out_path: Union[str, Path]):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        g = gdf.copy()
        for c in g.columns:
            if c != "geometry":
                g[c] = g[c].astype(object).where(pd.notna(g[c]), "")
        g.to_file(out_path, driver="ESRI Shapefile", encoding="utf-8")
        return out_path




    def clip_to_bbox(self,
                    gdf: Optional[gpd.GeoDataFrame],
                    region_bbox: Dict[str, float]) -> gpd.GeoDataFrame:
        """Recorta em WGS84, atualiza self.g_clipped. Se gdf=None, usa self.g_base."""
        if gdf is None:
            if self.g_base is None:
                raise ValueError("Nenhum GeoDataFrame base carregado. Chame combine_and_classify primeiro.")
            gdf = self.g_base
        clipped, meta = self._clip_by_bbox(gdf, region_bbox)
        if len(clipped) == 0:
            raise ValueError("O recorte removeu todas as feições.")
        self.g_clipped = clipped
        # recorte invalida cor/legenda anteriores (pois mixes mudam)
        self.color_map = None
        self.color_audit = {"area": meta, "groups": {}}
        self.legend_dict = None
        return clipped


    def build_color_map_from(self,
                            gdf: Optional[gpd.GeoDataFrame] = None,
                            attr: str = "coarse_grp") -> Tuple[Dict, Dict]:
        """Calcula o mapa de cores (e auditoria) com base no GDF fornecido (idealmente já recortado)."""
        if gdf is None:
            gdf = self.g_clipped if self.g_clipped is not None else self.g_base
        if gdf is None:
            raise ValueError("Sem dados. Use combine_and_classify (e clip_to_bbox se quiser) antes.")

        if attr != "coarse_grp":
            # suporte simples para outros attrs (cores de jitter)
            values = sorted(v for v in pd.unique(gdf[attr].astype("string")) if v not in (None, "", "nan"))
            cmap = {v: {"grp_color": jitter("#88AAFF", i, dh=0.25, dl=0.15)} for i, v in enumerate(values)}
            audit = {"note": f"cores geradas por jitter para attr='{attr}'"}
        else:
            values = sorted(v for v in pd.unique(gdf["coarse_grp"].astype("string")) if v not in (None, "", "nan"))
            cmap, audit = self._build_color_map_for_coarse(gdf, values)

        self.color_map = cmap
        # se já existia 'area' em self.color_audit (vinda do recorte), preserva + mescla
        if isinstance(self.color_audit, dict) and "area" in self.color_audit and "groups" in audit:
            audit = {"area": self.color_audit["area"], "groups": audit["groups"]}
        self.color_audit = audit
        return cmap, audit


    def simplified_legend_dict(self,
                            gdf: Optional[gpd.GeoDataFrame] = None,
                            group_attr: str = "coarse_grp",
                            youngest_first: bool = True) -> Dict:
        """
        Retorna um dicionário sem itens internos (apenas grupos) com cores e ordenação temporal.
        - youngest_first=True -> mais novos no topo (antigos na base).
        - Faz coerção robusta de IDADE_MAX / IDADE_MIN para float.
        """
        if gdf is None:
            gdf = self.g_clipped if self.g_clipped is not None else self.g_base
        if gdf is None:
            raise ValueError("Sem dados. Use combine_and_classify (e clip_to_bbox se quiser) antes.")
        if group_attr not in gdf.columns:
            raise KeyError(f"Coluna '{group_attr}' não encontrada no GeoDataFrame.")

        # garante mapa de cores coerente com o gdf
        uniq_groups = pd.unique(gdf[group_attr].astype("string"))
        if (self.color_map is None) or any(str(v) not in self.color_map for v in uniq_groups if v not in (None, "", "nan")):
            self.build_color_map_from(gdf, attr=group_attr)

        # --- limpeza robusta de idade ---
        def _coerce_age_series(s: pd.Series) -> pd.Series:
            if s is None:
                return pd.Series(np.nan, index=gdf.index)
            # para string: troca vírgula por ponto e extrai primeiro número
            ss = s.astype(str).str.replace(",", ".", regex=False)
            # tenta extrair o primeiro número "x" ou "x.y"
            num = ss.str.extract(r"(-?\d+(?:\.\d+)?)", expand=False)
            return pd.to_numeric(num, errors="coerce")

        have_age = ("IDADE_MAX" in gdf.columns) and ("IDADE_MIN" in gdf.columns)
        g = gdf.copy()
        if have_age:
            g["__ID_MAX__"] = _coerce_age_series(g["IDADE_MAX"])
            g["__ID_MIN__"] = _coerce_age_series(g["IDADE_MIN"])
            agg = (g.groupby(group_attr)
                    .agg(idade_max=("__ID_MAX__", "max"),
                        idade_min=("__ID_MIN__", "min"))
                    .reset_index())
        else:
            agg = (g.groupby(group_attr).size().reset_index(name="_n"))
            agg["idade_max"] = np.nan
            agg["idade_min"] = np.nan

        # helper: classifica cada grupo nos blocos desejados
        def _submacro_from_idade_code(idade_code: str):
            ic = str(idade_code).upper()
            if re.match(r"^A", ic):
                return "Pré-cambriano", "Arqueano"
            if re.match(r"^(PP|MP|NP)", ic):
                return "Pré-cambriano", "Proterozoico"
            if re.match(r"^(P(?!P)|D|C(?!C)|S|O|CM)", ic):
                return "Fanerozoico", "Paleozoico"
            if re.match(r"^(J|K|T|JK)", ic):
                return "Fanerozoico", "Mesozóico"
            if re.match(r"^(Q|N|PG|PL|PE|E)", ic):
                return "Fanerozoico", "Cenozoico"
            # fallback por macro simples
            macro = self._macro_from_idade_code_simple(ic)
            if macro in ("Pre-cambriano", "Pré-cambriano"):
                return "Pré-cambriano", "Proterozoico"
            if macro == "Paleozóico":
                return "Fanerozoico", "Paleozoico"
            if macro == "Mesozóico":
                return "Fanerozoico", "Mesozóico"
            if macro == "Cenozóico":
                return "Fanerozoico", "Cenozoico"
            return None, None

        buckets = {
            "Pré-cambriano": {"Arqueano": [], "Proterozoico": []},
            "Fanerozoico":   {"Paleozoico": [], "Mesozóico": [], "Cenozoico": []}
        }

        for _, row in agg.iterrows():
            grp = row[group_attr]
            if grp in (None, "", "nan"):
                continue
            idade_code = self._get_idade_code_from_grp(grp)
            major, sub = _submacro_from_idade_code(idade_code)
            if not major or not sub:
                continue
            cmv = self.color_map.get(str(grp))
            color = (cmv.get("grp_color", "") if isinstance(cmv, dict) else (cmv or "")) or "#DDDDDD"
            entry = {
                "group": str(grp),
                "idade_max": (float(row["idade_max"]) if pd.notna(row["idade_max"]) else None),
                "idade_min": (float(row["idade_min"]) if pd.notna(row["idade_min"]) else None),
                "color": color
            }
            buckets[major][sub].append(entry)

        # ordenação dos itens dentro de cada sub-subgrupo
        def key_young_first(e):
            imn = e["idade_min"]; imx = e["idade_max"]
            imn = imn if imn is not None else float("inf")
            imx = imx if imx is not None else float("inf")
            return (imn, imx)

        def key_old_first(e):
            imn = e["idade_min"]; imx = e["idade_max"]
            imn = imn if imn is not None else -float("inf")
            imx = imx if imx is not None else -float("inf")
            return (-imx, -imn)

        key_fn = key_young_first if youngest_first else key_old_first
        for maj in buckets:
            for sub in buckets[maj]:
                buckets[maj][sub].sort(key=key_fn)

        # ordem dos blocos e sub-blocos
        major_order = ["Fanerozoico", "Pré-cambriano"] if youngest_first else ["Pré-cambriano", "Fanerozoico"]
        pre_order   = ["Proterozoico", "Arqueano"]      if youngest_first else ["Arqueano", "Proterozoico"]
        pha_order   = ["Cenozoico", "Mesozóico", "Paleozoico"] if youngest_first else ["Paleozoico", "Mesozóico", "Cenozoico"]

        out = OrderedDict()
        for maj in major_order:
            sub_out = OrderedDict()
            if maj == "Pré-cambriano":
                for sub in pre_order:
                    sub_out[sub] = buckets[maj].get(sub, [])
            else:
                for sub in pha_order:
                    sub_out[sub] = buckets[maj].get(sub, [])
            out[maj] = sub_out

        self._simplified_legend_cache = out
        return out





    def build_legend_dict(self,
                        gdf: Optional[gpd.GeoDataFrame] = None,
                        group_attr: str = "coarse_grp",
                        sigla_col: Optional[str] = "SIGLA_UNID",
                        nome_col: str = "NOME_UNIDA",
                        hier_col: str = "HIERARQUIA") -> Dict:
        """Legenda hierárquica:
        Pré-cambriano → {Arqueano, Proterozoico}
        Fanerozoico   → {Paleozoico, Mesozóico, Cenozoico}
        Ordena grupos e itens por idade (IDADE_MAX/IDADE_MIN), mais antigos primeiro.
        """


        if gdf is None:
            gdf = self.g_clipped if self.g_clipped is not None else self.g_base
        if gdf is None:
            raise ValueError("Sem dados. Use combine_and_classify (e clip_to_bbox se quiser) antes.")

        # colunas auxiliares
        c_sigla = sigla_col if (sigla_col and sigla_col in gdf.columns) else \
                ("sigla" if "sigla" in gdf.columns else self.sigla_field)
        have_nome = nome_col in gdf.columns
        have_hier = hier_col in gdf.columns
        cols = [c for c in [c_sigla, nome_col, hier_col] if c in gdf.columns]
        if not cols:
            self.legend_dict = {"note": "colunas de legenda ausentes no GeoDataFrame"}
            return self.legend_dict

        age_max_col = "IDADE_MAX" if "IDADE_MAX" in gdf.columns else None
        age_min_col = "IDADE_MIN" if "IDADE_MIN" in gdf.columns else None

        # normalizadores de número (Ma). None/NaN -> None
        def _to_num(s):
            try:
                v = float(s)
                if math.isfinite(v):
                    return v
            except Exception:
                pass
            return None

        # decisão de macro e subsubgrupo
        def _main_sub_for_group(grp: str, sub_df: pd.DataFrame):
            # macro principal (pela 'macro_era' mais frequente, se existir)
            macro = ""
            if "macro_era" in sub_df.columns:
                vals = sub_df["macro_era"].astype("string")
                vals = [x for x in pd.unique(vals) if x and x.lower() != "nan"]
                if len(vals) == 1:
                    macro = vals[0]
                elif len(vals) > 1:
                    # maioria por contagem
                    macro = sub_df["macro_era"].mode(dropna=True)
                    macro = str(macro.iloc[0]) if len(macro) else ""

            # mapeia para chaves do JSON de topo
            if macro == "Pre-cambriano":
                main_key = "Pré-cambriano"
                # decide Arqueano/Proterozoico
                code = self._get_idade_code_from_grp(grp)
                if code.startswith("A"):
                    sub_key = "Arqueano"
                elif code.startswith(("NP","MP","PP")):
                    sub_key = "Proterozoico"
                else:
                    # fallback com EONs, se disponíveis
                    e_min = self.eon_min_field if self.eon_min_field in sub_df.columns else None
                    e_max = self.eon_max_field if self.eon_max_field in sub_df.columns else None
                    if e_min and e_max:
                        e_vals = pd.concat([sub_df[e_min], sub_df[e_max]]).astype("string")
                        e_vals = [str(x).upper() for x in e_vals if x and str(x).lower() != "nan"]
                        # se houver qualquer "ARQUEANO" e nenhum "PROTEROZOICO" -> Arqueano; caso contrário -> Proterozoico
                        if any("ARQUEANO" == e for e in e_vals) and not any("PROTEROZOICO" == e for e in e_vals):
                            sub_key = "Arqueano"
                        else:
                            sub_key = "Proterozoico"
                    else:
                        sub_key = "Proterozoico"
            elif macro in ("Paleozóico","Mesozóico","Cenozóico"):
                main_key = "Fanerozoico"
                # ajustes de acento conforme pedido
                sub_map = {"Paleozóico": "Paleozoico", "Mesozóico": "Mesozóico", "Cenozóico": "Cenozoico"}
                sub_key = sub_map.get(macro, macro)
            else:
                # sem macro conhecida: classifica como Fanerozoico por segurança
                main_key = "Fanerozoico"
                sub_key = "Paleozoico"
            return main_key, sub_key

        # estrutura inicial
        legend = {
            "Pré-cambriano": {"Arqueano": [], "Proterozoico": []},
            "Fanerozoico": {"Paleozoico": [], "Mesozóico": [], "Cenozoico": []}
        }

        # monta blocos por coarse_grp
        for grp, sub in gdf.groupby(group_attr, dropna=False):
            if grp in (None, "", "nan") or sub.empty:
                continue

            # itens (deduplicados) + idade por item para ordenar
            cols_items = cols.copy()
            if age_max_col: cols_items.append(age_max_col)
            if age_min_col: cols_items.append(age_min_col)
            df_items = sub[cols_items].copy()

            # normaliza strings
            for c in [c for c in [c_sigla, nome_col, hier_col] if c in df_items.columns]:
                df_items[c] = df_items[c].astype(object).where(pd.notna(df_items[c]), "").astype(str)

            # converte idades
            if age_max_col:
                df_items["__imax__"] = pd.to_numeric(df_items[age_max_col], errors="coerce")
            else:
                df_items["__imax__"] = pd.NA
            if age_min_col:
                df_items["__imin__"] = pd.to_numeric(df_items[age_min_col], errors="coerce")
            else:
                df_items["__imin__"] = pd.NA

            df_items = df_items.drop_duplicates()

            # ordena itens: mais antigos primeiro (IDADE_MAX desc, depois IDADE_MIN desc)
            df_items = df_items.sort_values(
                by=["__imax__","__imin__", c_sigla],
                ascending=[False, False, True],
                kind="mergesort"
            )

            items = []
            for _, r in df_items.iterrows():
                it = {
                    "sigla": r.get(c_sigla, ""),
                    "nome":  r.get(nome_col, "") if have_nome else "",
                    "hierarquia": r.get(hier_col, "") if have_hier else ""
                }
                # mantém idades no JSON se existirem
                if age_max_col: it["idade_max"] = (None if pd.isna(r["__imax__"]) else float(r["__imax__"]))
                if age_min_col: it["idade_min"] = (None if pd.isna(r["__imin__"]) else float(r["__imin__"]))
                items.append(it)

            if not items:
                continue

            # idade do grupo (envelope): max dos MAX e min dos MIN
            grp_iMax = None
            grp_iMin = None
            if age_max_col:
                v = pd.to_numeric(sub[age_max_col], errors="coerce")
                grp_iMax = (None if v.dropna().empty else float(v.max()))
            if age_min_col:
                v = pd.to_numeric(sub[age_min_col], errors="coerce")
                grp_iMin = (None if v.dropna().empty else float(v.min()))

            main_key, sub_key = _main_sub_for_group(str(grp), sub)

            legend[main_key].setdefault(sub_key, [])
            legend[main_key][sub_key].append({
                "group": str(grp),
                "idade_max": grp_iMax,
                "idade_min": grp_iMin,
                "items": items
            })

        # ordena cada lista por idade (mais antigos primeiro)
        def _age_key(d):
            # maior IDADE_MAX primeiro; em empate, maior IDADE_MIN
            imax = d.get("idade_max", None)
            imin = d.get("idade_min", None)
            imax = -1e99 if imax is None else imax
            imin = -1e99 if imin is None else imin
            return (-imax, -imin, d.get("group",""))

        for main_k, subdict in legend.items():
            for sub_k, arr in subdict.items():
                subdict[sub_k] = sorted(arr, key=_age_key)

        self.legend_dict = legend
        return legend



    def export_qml(self,
                qml_path: Union[str, Path],
                gdf: Optional[gpd.GeoDataFrame] = None,
                attr: str = "coarse_grp",
                source_gdf_for_mix: Optional[gpd.GeoDataFrame] = None):
        """Exporta o QML. Se não houver cmap calculado, calcula com o gdf fornecido."""
        if gdf is None:
            gdf = self.g_clipped if self.g_clipped is not None else self.g_base
        if gdf is None:
            raise ValueError("Sem dados para QML.")

        # se não foi pedido explicitamente, use o pré-dissolve recortado como base dos mixes
        if source_gdf_for_mix is None:
            source_gdf_for_mix = self.g_clipped if self.g_clipped is not None else self.g_base

        # garante que o color_map esteja coerente com o gdf
        if self.color_map is None:
            self.build_color_map_from(source_gdf_for_mix, attr="coarse_grp")

        return self.make_qml(gdf, qml_path, attr=attr, source_gdf_for_mix=source_gdf_for_mix)


    def export_legend_json(self, out_path: Union[str, Path], legend_dict: Optional[Dict] = None) -> Path:
        """Salva o dicionário de legenda (ou o atual em memória)."""
        out_path = Path(out_path)
        data = legend_dict if legend_dict is not None else (
            self.legend_dict if self.legend_dict is not None else {}
        )
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return out_path



    # ---------- Dissolve ----------
    @staticmethod
    def dissolve_by(gdf: gpd.GeoDataFrame, attr="coarse_grp") -> gpd.GeoDataFrame:

        if gdf is None or gdf.empty:
            return gpd.GeoDataFrame(gdf, crs=getattr(gdf, "crs", None))

        g = gdf.copy()

        # 1) conserta geometrias

        g = g.loc[g.geometry.notna() & (~g.geometry.is_empty)].copy()
        g["geometry"] = g.geometry.buffer(0)

        # 2) drop de colunas duplicadas (mantém a última ocorrência)
        if getattr(g, "columns", None) is not None:
            g = g.loc[:, ~g.columns.duplicated(keep="last")]

        if attr not in g.columns:
            raise KeyError(f"Coluna '{attr}' não encontrada para dissolve. Colunas: {list(g.columns)}")

        # 3) garanta que o 'grouper' seja 1-D
        grp_col = g[attr]
        if isinstance(grp_col, pd.DataFrame):
            grp_col = grp_col.iloc[:, 0]

        # 4) dissolver com coluna temporária para evitar colisão de nomes
        g = g.drop(columns=[attr], errors="ignore").assign(_grp=grp_col.values)

        out = g[["_grp", "geometry"]].dissolve(by="_grp").reset_index()
        out = out.rename(columns={"_grp": attr})

        return gpd.GeoDataFrame(out, crs=gdf.crs)


    def explode_multipart(
        self,
        gdf: gpd.GeoDataFrame | None = None,
        *,
        repair: bool = False,
        drop_empty: bool = True,
        id_col: str | None = None,
        keep_src_index: bool = True
    ) -> gpd.GeoDataFrame:
        """
        Converte Multi* em partes simples (explode), preservando atributos.
        Retorna GeoDataFrame com uma linha por parte.

        Parâmetros
        ----------
        gdf : GeoDataFrame de entrada; se None, usa self.g_clipped ou self.g_base.
        repair : se True, faz buffer(0) antes do explode (reparo rápido).
        drop_empty : se True, remove geometrias vazias/nulas antes do explode.
        id_col : se fornecido, cria coluna ID única por parte (ex.: '<attr>_part').
        keep_src_index : se True, mantém colunas '_src_idx' e '_part' (origem da parte).
        """
        if gdf is None:
            gdf = self.g_clipped if self.g_clipped is not None else self.g_base
        if gdf is None:
            raise ValueError("Sem dados. Use combine_and_classify (e clip_to_bbox se quiser) antes.")

        g = gdf.copy()

        if repair:
            g["geometry"] = g.geometry.buffer(0)

        if drop_empty:
            g = g.loc[g.geometry.notna() & (~g.geometry.is_empty)].copy()

        exploded = g.explode(index_parts=True, ignore_index=False)

        # Se não virou MultiIndex (caso sem multipartes), padroniza
        if not isinstance(exploded.index, pd.MultiIndex):
            exploded.index = pd.MultiIndex.from_arrays(
                [exploded.index, pd.Series(0, index=exploded.index, dtype=int)],
                names=["_src_idx", "_part"]
            )

        exploded = exploded.reset_index(names=["_src_idx", "_part"])

        if id_col:
            exploded[id_col] = exploded["_src_idx"].astype(str) + "_" + exploded["_part"].astype(str)

        if not keep_src_index:
            exploded = exploded.drop(columns=["_src_idx", "_part"], errors="ignore")

        gdf_out = gpd.GeoDataFrame(exploded, geometry="geometry", crs=gdf.crs)
        return gdf_out
