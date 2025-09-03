import re
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from cartopy.io import shapereader as shpreader
import matplotlib.transforms as mtransforms
from matplotlib import colors as mcolors





def _label_projected_grid(ax, projection, xlocs, ylocs, pad_pts=12):
    xmin, xmax, ymin, ymax = ax.get_extent(crs=projection)

    def _fmt(v):
        return f"{v/1000:.0f} km" if abs(v) >= 1000 else f"{v:.0f} m"

    base = projection._as_mpl_transform(ax)
    off_bottom = mtransforms.ScaledTranslation(0, -pad_pts/72., ax.figure.dpi_scale_trans)
    off_left   = mtransforms.ScaledTranslation(-pad_pts/72., 0, ax.figure.dpi_scale_trans)
    t_bottom, t_left = base + off_bottom, base + off_left
    txt_kw = dict(fontsize=9, zorder=4, clip_on=False)

    for x in xlocs:
        if xmin <= x <= xmax:
            ax.text(x, ymin, _fmt(x), ha='center', va='top', transform=t_bottom, **txt_kw)
    for y in ylocs:
        if ymin <= y <= ymax:
            ax.text(xmin, y, _fmt(y), ha='right', va='center', transform=t_left, **txt_kw)

def _nice_step(span, target=5):
    """Escolhe passo 1–2–5*10^n para cobrir 'span' com ~target intervalos."""
    if not np.isfinite(span) or span <= 0:
        return 1.0
    raw = span / max(target, 1)
    exp = np.floor(np.log10(raw))
    frac = raw / (10 ** exp)
    if frac < 1.5: step = 1
    elif frac < 3: step = 2
    elif frac < 7: step = 5
    else: step = 10
    return step * (10 ** exp)

import cartopy.crs as ccrs
try:
    from pyproj import CRS as ProjCRS
except Exception:
    ProjCRS = None

def to_cartopy_crs(crs_like):
    """Aceita ccrs.CRS, int EPSG (ex.: 5880) ou string 'EPSG:XXXX'/'PlateCarree'."""
    if crs_like is None:
        return ccrs.PlateCarree()
    if isinstance(crs_like, ccrs.CRS):
        return crs_like
    if isinstance(crs_like, int):
        code = crs_like
        if ProjCRS is not None:
            if ProjCRS.from_epsg(code).is_geographic:
                return ccrs.PlateCarree()
            return ccrs.epsg(code)
        if code in (4326, 4674):  # geográficos comuns
            return ccrs.PlateCarree()
        return ccrs.epsg(code)
    if isinstance(crs_like, str):
        s = crs_like.strip().upper()
        if s in {"PLATECARREE", "PLATE_CARREE", "GEODETIC", "WGS84", "EPSG:4326", "EPSG:4674"}:
            return ccrs.PlateCarree()
        if s.startswith("EPSG:"):
            return ccrs.epsg(int(s.split(":")[1]))
    raise ValueError(f"CRS não reconhecido: {crs_like!r}")



def plot_geodf_by_simplified_legend(
    gdf,
    simplified_legend,
    group_attr="coarse_grp",
    title=None,
    data_crs=None,           # se None, usa gdf.crs
    projection=None,         # se None, PlateCarree()
    figure_path=None,
    face_alpha=0.95,
    edgecolor="none",        # sem contorno
    linewidth=0.0,           # sem contorno
    show_states=False,
    states_resolution="110m",
    extent=None,               # [xmin, xmax, ymin, ymax] no CRS de data_crs
    grid_in_projection=None,   # None=auto; True=grade no CRS projetado; False=graus
    grid_label_pad_pts=12,     # afastamento dos rótulos (quando projetado)
    # ---- legenda ----
    legend_outside=True,
    legend_marker_size=6,
    legend_cols=1,
    legend_right_frac=0.82,    # fração reservada ao MAPA (resto é legenda)
    legend_h="right",          # "left" | "right"
    legend_v="up"              # "up"   | "down"
):

    def _round_floor(x, step): return step * np.floor(x / step)
    def _round_ceil(x, step):  return step * np.ceil(x / step)


    # -------- CRS --------
    proj = to_cartopy_crs(projection) if 'to_cartopy_crs' in globals() else (projection or ccrs.PlateCarree())
    if data_crs is None: data_crs = gdf.crs
    data_crs = to_cartopy_crs(data_crs) if 'to_cartopy_crs' in globals() else ccrs.PlateCarree()

    # -------- rótulos (símbolos e limpeza) --------
    greek_patterns = [
        (r"(?<![A-Za-z])alfa(?![A-Za-z])",   "α"),
        (r"(?<![A-Za-z])alpha(?![A-Za-z])",  "α"),
        (r"(?<![A-Za-z])beta(?![A-Za-z])",   "β"),
        (r"(?<![A-Za-z])gama(?![A-Za-z])",   "γ"),
        (r"(?<![A-Za-z])gamma(?![A-Za-z])",  "γ"),
        (r"(?<![A-Za-z])delta(?![A-Za-z])",  "δ"),
        (r"(?<![A-Za-z])lambda(?![A-Za-z])", "λ"),
        (r"(?<![A-Za-z])mu(?![A-Za-z])",     "μ"),
    ]
    # -------- rótulos (símbolos e limpeza) --------
    def _label_from_group(grp):
        s = str(grp).strip()
        # Cambriano: aceita C_cortado, C-cortado, C_cortado_...
        s = re.sub(r"(?i)C[_-]?CORTADO_?", "Є", s)
        # cola tudo
        s = s.replace("|", "").replace("_", "")
        # gregos (substitui em QUALQUER posição, sem limite de palavra)
        greek_map = {
            "alfa": "α", "alpha": "α",
            "beta": "β",
            "gama": "γ", "gamma": "γ",
            "delta": "δ",
            "lambda": "λ",
            "mu": "μ",
        }
        for k, v in greek_map.items():
            s = re.sub(k, v, s, flags=re.IGNORECASE)
        return s


    # ==============================================================
    # 1) ORDEM DE PLOT (com Cenozoico incluído, para cores corretas):
    #    Arqueano → Proterozoico → Paleozoico → Mesozóico → Cenozoico
    pre = simplified_legend.get("Pré-cambriano", {})
    fan = simplified_legend.get("Fanerozoico", {})

    plot_blocks = []
    for sub in ("Arqueano", "Proterozoico"):           # Pré-cambriano
        if pre.get(sub): plot_blocks.append(pre[sub])
    for sub in ("Paleozoico", "Mesozóico", "Cenozoico"):  # Fanerozoico
        if fan.get(sub): plot_blocks.append(fan[sub])

    # mapeia cores conhecidas (de TODOS os blocos, inclusive Cenozoico)
    known_colors = {}
    for items in plot_blocks:
        for it in items:
            gkey = str(it.get("group", "")).strip()
            if gkey: known_colors[gkey] = it.get("color", "#DDDDDD")

    # grupos presentes no dado
    present_groups = set(str(v).strip() for v in gdf[group_attr].astype(str).fillna(""))

    # sequência final para PLOT
    categories_plot = [str(it["group"]).strip() for items in plot_blocks for it in items
                       if str(it.get("group","")).strip() in present_groups]
    # adiciona qualquer grupo “faltante” (ex.: não listado no simplified) no fim
    listed = set(categories_plot)
    for gname in sorted(present_groups - listed):
        categories_plot.append(gname)
    color_by_group = {g: known_colors.get(g, "#DDDDDD") for g in categories_plot}

    # ==============================================================
    # 2) CONTEÚDO DA LEGENDA (Cenozoico OMITIDO) + subtítulo “Arqueano”
    legend_sections = []
    # Fanerozoico: só Mesozóico e Paleozoico
    if fan.get("Mesozóico"):
        legend_sections.append(("Mesozóico",  [it for it in fan["Mesozóico"]
                                               if str(it.get("group","")).strip() in present_groups]))
    if fan.get("Paleozoico"):
        legend_sections.append(("Paleozoico", [it for it in fan["Paleozoico"]
                                               if str(it.get("group","")).strip() in present_groups]))
    # Pré-cambriano: Proterozoico E Arqueano COM títulos
    if pre.get("Proterozoico"):
        legend_sections.append(("Proterozoico", [it for it in pre["Proterozoico"]
                                                 if str(it.get("group","")).strip() in present_groups]))
    if pre.get("Arqueano"):
        legend_sections.append(("Arqueano",    [it for it in pre["Arqueano"]
                                                 if str(it.get("group","")).strip() in present_groups]))

    # ==============================================================
    # 3) FIGURA / EXTENT
    fig, ax = plt.subplots(subplot_kw={"projection": proj}, figsize=(10.5, 8.5))
    if extent is not None:
        ax.set_extent(extent, crs=data_crs)
    else:
        try:
            minx, miny, maxx, maxy = gdf.total_bounds
            ax.set_extent([minx, maxx, miny, maxy], crs=data_crs)
        except Exception:
            pass

    # 4) PLOT POLÍGONOS (mais antigos primeiro)
    for z, grp in enumerate(categories_plot, start=1):
        sub = gdf[gdf[group_attr].astype(str).str.strip() == grp]
        if len(sub) == 0: continue
        face = mcolors.to_rgba(color_by_group.get(grp, "#DDDDDD"), alpha=face_alpha)
        ax.add_geometries(sub.geometry, crs=data_crs,
                          facecolor=face, edgecolor=edgecolor, linewidth=linewidth, zorder=1+z)

    # --------- features de base ---------
    ax.add_feature(cfeature.COASTLINE, linewidth=0.6, zorder=7)
    ax.add_feature(cfeature.BORDERS,   linewidth=0.5, zorder=7)

    if show_states:
        # tenta (res pedidos) -> 50m -> 10m; e lines -> polygons
        tried = []
        for res in [states_resolution, "50m", "10m"]:
            for name in ["admin_1_states_provinces_lines", "admin_1_states_provinces"]:
                try:
                    shpfile = shpreader.natural_earth(resolution=res, category="cultural", name=name)
                    reader = shpreader.Reader(shpfile)
                    geoms = list(reader.geometries())
                    if geoms:
                        ax.add_geometries(
                            geoms, crs=ccrs.PlateCarree(),
                            facecolor="none", edgecolor="k",
                            linewidth=0.9, zorder=10
                        )
                        raise StopIteration  # saiu dos dois loops
                except StopIteration:
                    break
                except Exception as e:
                    tried.append((res, name, str(e)))
            else:
                continue
            break


    # 6) GRADE
    is_geo_projection = isinstance(proj, (ccrs.PlateCarree, ccrs.Geodetic))
    if grid_in_projection is None: grid_in_projection = not is_geo_projection
    if grid_in_projection and not is_geo_projection:
        xmin, xmax, ymin, ymax = ax.get_extent(crs=proj)
        stepx = _nice_step(xmax-xmin, target=5); stepy = _nice_step(ymax-ymin, target=5)
        if not (np.isfinite(stepx) and np.isfinite(stepy) and stepx>0 and stepy>0):
            stepx = stepy = 100000.0
        x0 = _round_floor(xmin, stepx); x1 = _round_ceil(xmax, stepx)
        y0 = _round_floor(ymin, stepy); y1 = _round_ceil(ymax, stepy)
        xlocs = np.arange(x0, x1 + 0.5*stepx, stepx)
        ylocs = np.arange(y0, y1 + 0.5*stepy, stepy)
        ax.gridlines(crs=proj, draw_labels=False, xlocs=xlocs, ylocs=ylocs,
                     linestyle='--', alpha=0.3)
        _label_projected_grid(ax, proj, xlocs, ylocs, pad_pts=grid_label_pad_pts)
    else:
        from cartopy.mpl.ticker import LongitudeFormatter, LatitudeFormatter
        gl = ax.gridlines(draw_labels=True, linestyle='--', alpha=0.3)
        gl.xformatter = LongitudeFormatter(); gl.yformatter = LatitudeFormatter()
        gl.top_labels = False; gl.right_labels = False

    # 7) LEGENDA (sem Cenozoico + títulos inclusive para Arqueano)
    handles, header_flags = [], []
    for header, items in legend_sections:
        if items:
            # título do bloco
            h = Line2D([], [], linestyle='None', marker=None, label=header)
            handles.append(h); header_flags.append(True)
            # itens
            for it in items:
                gname = str(it["group"]).strip()
                lab   = _label_from_group(gname)
                col   = color_by_group.get(gname, it.get("color", "#DDDDDD"))
                h = Line2D([0], [0], marker='s', linestyle='None',
                           markersize=legend_marker_size,
                           markerfacecolor=col, markeredgecolor=edgecolor, label=lab)
                handles.append(h); header_flags.append(False)

    # posicionamento
    corner_loc = {("left","up"):"upper left", ("right","up"):"upper right",
                  ("left","down"):"lower left", ("right","down"):"lower right"}
    if legend_outside:
        if legend_h == "right":
            fig.tight_layout(rect=[0, 0, legend_right_frac, 1])
            loc = "upper left" if legend_v=="up" else "lower left"
            x_anchor = 1.02; y_anchor = 1.0 if legend_v=="up" else 0.0
        elif legend_h == "left":
            fig.tight_layout(rect=[1 - legend_right_frac, 0, 1, 1])
            loc = "upper right" if legend_v=="up" else "lower right"
            x_anchor = -0.02; y_anchor = 1.0 if legend_v=="up" else 0.0
        else:
            if legend_v == "up":
                fig.tight_layout(rect=[0, 0, 1, 1 - legend_right_frac])
                loc = "lower center"; x_anchor, y_anchor = 0.5, 1.02
            else:
                fig.tight_layout(rect=[0, legend_right_frac, 1, 1])
                loc = "upper center"; x_anchor, y_anchor = 0.5, -0.02
        leg = ax.legend(handles=handles, title="Legenda",
                        loc=loc, bbox_to_anchor=(x_anchor, y_anchor),
                        borderaxespad=0., frameon=True, fontsize=8, ncol=legend_cols,
                        handlelength=1.2, columnspacing=0.8, handletextpad=0.4)
    else:
        leg = ax.legend(handles=handles, title="Legenda",
                        loc=corner_loc[(legend_h, legend_v)],
                        frameon=True, fontsize=8, ncol=legend_cols,
                        handlelength=1.2, columnspacing=0.8, handletextpad=0.4)

    # deixa títulos dos blocos em negrito e sem quadradinho
    for txt, is_hdr, hnd in zip(leg.get_texts(), header_flags, leg.legend_handles):
        if is_hdr:
            txt.set_weight("bold")
            hnd.set_visible(False)

    # título / salvar
    if title: ax.set_title(title)
    ax.set_aspect('equal', adjustable='box')
    if figure_path:
        fig.savefig(figure_path, dpi=300, bbox_inches="tight")
    plt.show()
