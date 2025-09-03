# 🌍 Geomageddon

Pipeline em Python para **unificar mapeamentos geológicos**, **classificar por grupos coerentes** (a partir de *SIGLA*), **gerar paletas e QMLs do QGIS** com mistura de cores ponderada por área, e **produzir legendas JSON** ordenadas por tempo geológico. Inclui utilitários de escala cartográfica e poda de polígonos pequenos em função da escala do layout — e um módulo de **plotagem** para gerar mapas prontos direto do `GeoDataFrame`.

> TL;DR: junte seus shapefiles, classifique por grupos (`coarse_grp`), recorte por bbox, dissolva, **exporte QML + JSON de legenda**… ou pule direto para a **plotagem** com uma legenda simplificada. Sem derramar café nos QMLs.

---

## 📂 Estrutura do repositório

```
geomageddon/
├── code/
│   ├── geomageddon.py   # classe e pipeline principais (GeoSiglaStyler)
│   └── plotagem.py      # funções de plotagem (matplotlib/geopandas)
├── notebooks/
│   └── geological_map.ipynb   # notebook de testes/exemplos
└── data/                # dados de entrada/saída organizados por estado
    ├── sig_geologia_estado_do_parana_vf/
    ├── sig_mato_grosso_do_sul/
    ├── sig_minas_gerais/
    ├── sig_rio_grande_do_sul/
    ├── sig_santa_catarina/
    └── sig_sao_paulo/
```

Cada subdiretório de `data` contém camadas de um SIG de geologia regional distribuído pela **CPRM/SGB** (apenas para exemplo). Verifique a **licença/uso** dos dados na fonte.

---

## Sumário

* [Principais recursos](#principais-recursos)
* [Instalação](#instalação)
* [Pré‑requisitos e suposições de dados](#pré-requisitos-e-suposições-de-dados)
* [Exemplo rápido (5 minutos)](#exemplo-rápido-5-minutos)
* [Fluxo típico de trabalho](#fluxo-típico-de-trabalho)
* [Estratégia de cores](#estratégia-de-cores)

  * [De onde vêm as cores?](#de-onde-vêm-as-cores)
  * [Auditoria de cores (JSON)](#auditoria-de-cores-json)
* [Plotagem com `plotagem.py`](#plotagem-com-plotagempy)
* [Legenda JSON](#legenda-json)
* [Escala e poda de partes pequenas](#escala-e-poda-de-partes-pequenas)
* [API de alto nível](#api-de-alto-nível)
* [Dicas de desempenho](#dicas-de-desempenho)
* [Solução de problemas](#solução-de-problemas)
* [Compatibilidade QGIS](#compatibilidade-qgis)
* [Licença](#licença)

---

## Principais recursos

* **Merge** de múltiplos `GeoDataFrame`s com alinhamento automático de CRS.
* **Classificação** a partir de `SIGLA_UNID` (ou equivalente) gerando:

  * `idade_code`, `greek`, `stem` e **`coarse_grp`**;
  * lógica de **“dominó”** com **EON** e **ERA** → `macro_era` consistente;
  * fusão opcional por **NOME\_UNIDA** (puxa para o grupo dominante por área/contagem);
  * opção de **colapsar todo o Cenozóico** em um único grupo.
* **Cores inteligentes**:

  1. Mistura ponderada por área a partir de **QML(s)** de referência (SIGLA → cor);
  2. *Fallback* por **CLASSE\_ROC** e **CLASSE\_R\_1** (também ponderado por área);
  3. *Fallback* por **idade** (paleta geocronológica);
  4. *Jitter* suave para desempatar cores idênticas.
* **Exportação de QML** (renderer *categorized*) e **JSON de auditoria** das cores.
* **Legendas JSON** (completo e simplificado) ordenadas por **idade** (opções *youngest‑first* ou *oldest‑first*).
* **Recorte por *bbox*** em WGS84 com *fallback* robusto.
* **Escala cartográfica** a partir da **largura da figura** (mm/cm/in/px) com arredondamento *nice*.
* **Poda de polígonos pequenos** com base na **área física em mm²** no layout (cKDTree/STRtree/sjoin).
* **Plotagem pronta**: gere figuras com `plotagem.py` usando a **legenda simplificada**.

---

## Instalação

Recomendado **Python ≥ 3.10**.

Clone o repositório e instale as dependências com `conda` ou `pip`:

```bash
git clone https://github.com/<usuario>/geomageddon.git
cd geomageddon
conda env create -f environment.yml
conda activate geomageddon
```

Ou instale manualmente:

```bash
pip install geopandas shapely numpy pandas scipy matplotlib
```

> Você não precisa do QGIS para rodar o pipeline, **apenas para visualizar** os `.qml` exportados.

---

## Pré‑requisitos e suposições de dados

A classe assume, por padrão, nomes de colunas comuns em bases da CPRM. Personalize no `__init__` se os seus forem diferentes:

| Papel              | Coluna padrão              |
| ------------------ | -------------------------- |
| Sigla da unidade   | `SIGLA_UNID`               |
| Nome da unidade    | `NOME_UNIDA`               |
| EON mínimo/máximo  | `EON_IDAD_1`, `EON_IDAD_M` |
| ERA mínima/máxima  | `ERA_MINIMA`, `ERA_MAXIMA` |
| Hierarquia         | `HIERARQUIA`               |
| Classe ROC         | `CLASSE_ROC`               |
| Classe R1          | `CLASSE_R_1`               |
| Idade min/max (Ma) | `IDADE_MIN`, `IDADE_MAX`   |

> **CRS**: para cálculos de área (mistura ponderada e poda), forneça um **CRS em metros** via `area_crs` (ex.: *Albers Equal Area*). Sem CRS, o código cai em pesos iguais (avisa no metadata).

---

## Exemplo rápido (5 minutos)

```python
import geopandas as gpd
from code.geomageddon import GeoSiglaStyler  # classe principal

# 1) Dados de entrada
br_pr = gpd.read_file("/caminho/br_pr.shp")
br_sc = gpd.read_file("/caminho/br_sc.shp")

sty = GeoSiglaStyler(
    area_crs="EPSG:5880",         # Albers Brasil (exemplo) ou outro CRS métrico
    area_weighting=True,           # mistura de cores ponderada por área
)

# 2) Combinar e classificar
base = sty.combine_and_classify(gdfs=[br_pr, br_sc], enforce_mode="flag")

# 3) Recorte (opcional)
sty.clip_to_bbox(base, {
    "min_lon": -54, "max_lon": -45,
    "min_lat": -30, "max_lat": -22,
})

# 4) Dissolver por grupo coerente
parts = sty.dissolve_by_attr(attr="coarse_grp")

# 5) Cores + QML (usa base pré-dissolve para misturas)
cmap, audit = sty.build_color_map_from(gdf=sty.g_clipped, attr="coarse_grp")
sty.export_qml("out/mantiqueira.qml", gdf=parts, source_gdf_for_mix=sty.g_clipped)

# 6) Legendas (completa e simplificada)
full_legend = sty.build_legend_dict(parts)
sty.export_legend_json("out/legend.full.json", full_legend)

simp_legend = sty.simplified_legend_dict(parts, youngest_first=True)
sty.export_legend_json("out/legend.simple.json", simp_legend)

# 7) (Opcional) Poda por escala e salvar shapefile
parts_pruned = sty.cull_small_parts_by_scale(parts, min_area_mm2=1.0)
sty.save_shp(parts_pruned, "out/mantiqueira_dissolved.shp")
```

Abra o `mantiqueira.qml` no QGIS e aplique na camada dissolvida. Ou siga para a **seção de plotagem** para gerar a figura direto em Python.

---

## Estratégia de cores

### De onde vêm as cores?

A escolha da cor do grupo (`grp_color`) segue **prioridades**:

1. **QML(s) de referência** (*SIGLA → #RRGGBB*). O método seleciona automaticamente o **atributo mais coberto** por área (tipicamente `sigla`) e mistura as cores das SIGLAs do grupo;
2. **CLASSE\_ROC** (mistura ponderada por área);
3. **CLASSE\_R\_1** (mistura ponderada por área);
4. **Idade** (paleta geocronológica embutida);
5. **Jitter** sutil para desempatar grupos que ainda ficaram com a mesma cor.

> O peso de cada categoria é calculado por **área no CRS métrico** (ou pesos iguais, na falta de CRS). Configure `area_crs` e mantenha geometrias válidas.

### Auditoria de cores (JSON)

Ao gerar o QML via `make_qml`/`export_qml`, um arquivo `*.audit.json` é salvo com detalhes por grupo, incluindo mixes por SIGLA/ROC/R1 e instantâneos das rodadas de desempate por cor.

---

## Plotagem com `plotagem.py`

O módulo `plotagem.py` fornece funções utilitárias para criar figuras diretamente a partir do `GeoDataFrame`. A função principal é:

**`plot_geodf_by_simplified_legend(gdf, legend, *, group_attr="coarse_grp", title=None, projection="EPSG:4674", data_crs="EPSG:4674", figure_path=None, show_states=False, states_resolution="50m", legend_outside=True, legend_cols=4, legend_h="right", legend_v="up")`**

* **Entrada `legend`**: use exatamente o dicionário retornado por `GeoSiglaStyler.simplified_legend_dict(...)`.
* **CRS**: `projection` e `data_crs` permitem projetar/no reprojetar os dados para a figura.
* **Legenda**: pode ser externa (`legend_outside=True`), com colunas configuráveis e ancoragem horizontal/vertical.
* **Saída**: se `figure_path` for fornecido, salva a figura (ex.: PNG); caso contrário, exibe em tela.

### Exemplo de uso

```python
from code.geomageddon import GeoSiglaStyler
from code import plotagem as plot

# Prepare dados e cores (como no exemplo rápido)
cmap, audit = sty.build_color_map_from(gdf=sty.g_clipped, attr="coarse_grp")
simp = sty.simplified_legend_dict(gdf=sty.g_clipped)

plot.plot_geodf_by_simplified_legend(
    sty.g_clipped,
    simp,
    group_attr="coarse_grp",
    title="Mapa litológico",
    projection="EPSG:4674",
    data_crs="EPSG:4674",
    figure_path="data/out/g_diss.png",
    show_states=False,
    states_resolution="50m",
    legend_outside=True,
    legend_cols=4,
    legend_h="right",          # "left" | "right"
    legend_v="up"              # "up"   | "down"
)
```

> O notebook `notebooks/geological_map.ipynb` traz um fluxo completo (do merge à plotagem) usando shapefiles de exemplo da CPRM.

---

## Legenda JSON

Há duas formas principais:

1. **Completa** (`build_legend_dict`) — estrutura hierárquica por **Pré‑cambriano/Fanerozoico** e sub‑blocos (Arqueano/Proterozoico/Paleozoico/Mesozóico/Cenozoico), com **itens** (sigla, nome, hierarquia) e envelope de **idades** do grupo.
2. **Simplificada** (`simplified_legend_dict`) — apenas os **grupos** com `color`, `idade_max`, `idade_min`, organizados temporalmente. Ideal para construir uma **legenda compacta** no layout e para **dirigir a plotagem** via `plotagem.py`.

Salve qualquer uma com `export_legend_json("legend.json", data)`.

---

## Escala e poda de partes pequenas

* `width_to_scale(...)` calcula **1\:N** a partir da **largura da figura** (mm/cm/in/px), margem e CRS. Usa arredondamento *nice* (`1, 2, 2.5, 5 × 10^k`).
* `cull_small_parts_by_scale(...)` remove partes cuja **área física** (na escala corrente) seja menor que `min_area_mm2` (padrão 1 mm²), **reagregando** cada peça ao vizinho mais apropriado (que toca ou é mais próximo, desempate por maior área).

  * Rápido com **SciPy cKDTree**; *fallbacks*: **Shapely STRtree**, `sjoin`, `sjoin_nearest`.

> Requer **CRS métrico**. Se indisponível, é levantada uma exceção explicativa.

---

## API de alto nível

Principais métodos (nomes abreviados; veja *docstrings* no código para detalhes):

* **Carga e pré‑processo**

  * `merge(gdfs)` — concatena GDFs alinhando CRS.
  * `combine_and_classify(gdfs|in_gdf, ..., enforce_mode)` — gera `coarse_grp`, `macro_era` e afins.
  * `clip_to_bbox(gdf, {min_lon,max_lon,min_lat,max_lat})` — recorta em WGS84 (fallback robusto).
* **Dissolve e atributos**

  * `dissolve_by_attr(gdf, attr="coarse_grp")` — dissolve preservando textos únicos e regras de idade/EON/ERA.
* **Cores, QML e legendas**

  * `build_color_map_from(gdf, attr="coarse_grp")` — cria mapa de cores e auditoria.
  * `make_qml(gdf, qml_path, attr, source_gdf_for_mix)` / `export_qml(...)` — salva `.qml` (renderer *categorized*).
  * `build_legend_dict(gdf)` / `simplified_legend_dict(gdf, youngest_first=True)` — dicionários de legenda.
  * `export_legend_json(path, data=None)` — salva JSON de legenda.
* **Escala e poda**

  * `width_to_scale(gdf, fig_width, width_unit, ...)` — retorna `N` ou `(N, meta)`.
  * `cull_small_parts_by_scale(gdf, min_area_mm2=1.0, ...)` — remove partes pequenas com união guiada por vizinhança.
* **IO**

  * `save_shp(gdf, out_path)` — exporta *ESRI Shapefile* com strings normalizadas.

---

## Dicas de desempenho

* Instale **SciPy** para acelerar a poda por KDTree (`pip install scipy`).
* Se a base for muito grande, ative o `clip_to_bbox` antes de dissolver/colorir.
* Ajuste `area_weighting=False` para testes rápidos (mistura por contagem em vez de área).
* Ajuste `auto_cull_small_parts` e `min_area_mm2` conforme a escala alvo do layout.

---

## Solução de problemas

* **“gdf.crs ausente; pesos=1.0”**: defina `area_crs` no construtor ou atribua um CRS projetado ao GDF antes dos cálculos.
* **“Não dá para podar por escala sem CRS métrico”**: a poda exige áreas em m²; forneça `area_crs`.
* **QML sem cores**: verifique se o(s) QML(s) de referência foram carregados (padrão: auto‑busca por `*lito.qml` em `/mnt/data`). Carregue manualmente: `load_sigla_qml(["/caminho/a.qml"])`.
* **Campos ausentes**: personalize os nomes no `__init__` (ex.: `roc_field="CLASSE_ROC"`).

---

## Compatibilidade QGIS

O QML gerado usa `renderer-v2 type="categorizedSymbol"` e propriedades padrão de preenchimento/borda. Testado com **QGIS 3.28+** (formato estável). Caso seu estilo use `Option name="color"`, o parser também reconhece esse padrão.

---

## Licença

MIT License. Use, modifique e destrua polígonos livremente.

---

**Sugestões e *PRs* são bem‑vindos.** Se algo quebrar, culpe a **geometria inválida**… ou o Cambriano `C_CORTADO_`, que sempre aparece onde menos se espera.
