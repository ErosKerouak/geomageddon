
# 🌍 Geomageddon

**Geomageddon** é um algoritmo em Python para processamento de shapefiles que declara guerra às feições insignificantes.
Ele elimina polígonos minúsculos que só servem para poluir a legenda e sobrecarregar sua renderização, fundindo-os de forma inteligente aos vizinhos maiores.

---

## 📂 Estrutura do repositório

A organização do projeto segue uma lógica simples:

```
geomageddon/
├── code/        # scripts Python principais
└── data/        # dados de entrada/saída organizados por estado
    ├── sig_geologia_estado_do_parana_vf/
    ├── sig_mato_grosso_do_sul/
    ├── sig_minas_gerais/
    ├── sig_rio_grande_do_sul/
    ├── sig_santa_catarina/
    └── sig_sao_paulo/
```

Cada subdiretório de `data` contém camadas típicas de um SIG geológico, como **Litologia**, **Estruturas**, **Hidrografia**, etc.
Essa estrutura foi inspirada diretamente na forma como a **CPRM/SGB** distribui seus shapefiles de geologia regional.

---

## Padrão de atributos

Por padrão, o `geomageddon` espera que os shapefiles de entrada sigam a convenção de nomes usada pela **SGB**.
Alguns exemplos:

* `"SIGLA_UNID"` → código da unidade geológica
* `"NOME_UNIDA"` → nome da unidade geológica
* `"HIERARQUIA"` → nível hierárquico (ex.: Grupo, Formação)

Se seus dados tiverem nomes diferentes, é possível adaptar a classe principal (`GeoSiglaStyler`) passando os campos corretos no `__init__`.

---


## Features

* **Culling de feições pequenas**: remove polígonos abaixo de um limiar definido em **mm² na escala da figura**.
* **Fusão automática**: polígonos minúsculos são mesclados ao maior vizinho.
* **Escala adaptativa**: calcula o limiar em função da largura da figura e da escala.
* **Alta performance**: usa `cKDTree` ou `STRtree` (quando disponível) para encontrar vizinhos rapidamente.
* **Preserva o essencial**: resultado é um mapa mais limpo, com legenda legível e polígonos significativos.

---

## 🛠️ Instalação

Clone o repositório e instale as dependências com `conda` ou `pip`:

```bash
git clone https://github.com/<usuario>/geomageddon.git
cd geomageddon
conda env create -f environment.yml
conda activate geomageddon
```

Ou instale manualmente:

```bash
pip install geopandas shapely numpy pandas scipy
```

---

## Uso básico

```python
import geopandas as gpd
from geomageddon import GeoSiglaStyler  # exemplo de classe principal

gdf = gpd.read_file("meu_mapa.shp")

styler = GeoSiglaStyler(area_crs="EPSG:5880")  # Albers Equal Area para Brasil
gdf_clean = styler.cull_small_parts_by_scale(gdf)

gdf_clean.to_file("meu_mapa_limpo.shp")
```

---

## Como funciona

1. Converte sua figura (em mm/cm/in/px) para escala 1\:N.
2. Calcula o **limiar mínimo de área visível** no papel (1 mm² → \~N² m² no terreno).
3. Classifica polígonos em `big` (significativos) e `small` (dispensáveis).
4. Para cada `small`:

   * procura vizinho por interseção;
   * se não encosta em ninguém, usa vizinho mais próximo;
   * funde tudo ao vizinho maior.

---

## Avisos

* Geomageddon não liga para atributos: se for pequeno demais, vai pro vizinho.
* Se você realmente ama aquele polígono de 0.0001 mm², guarde ele antes.
* O algoritmo pode ser mais lento em CRS geográficos — prefira **equal-area projections**.

---

## Contribuindo

Pull requests são bem-vindos! Sugestões de melhorias, nomes mais épicos para funções ou novas estratégias de fusão são aceitas.

---

## Licença

MIT License. Use, modifique e destrua polígonos livremente.

---
