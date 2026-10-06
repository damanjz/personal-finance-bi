"""Generate the Power BI project (PBIP): TMDL semantic model + PBIR report.

The model imports the CSVs in data/model. Column types come from the DuckDB
schema, so re-running the pipeline and this script keeps the two in sync.
"""
import argparse
import hashlib
import json
import shutil
import uuid
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "powerbi"
NAME = "FinanceBI"
SM = OUT / f"{NAME}.SemanticModel"
RP = OUT / f"{NAME}.Report"
DATA_FOLDER = str(ROOT / "data" / "model") + "\\"
PORTABLE_DATA_FOLDER = "C:\\personal-finance-bi\\data\\model\\"

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"

# ---------------------------------------------------------------- palette
INK, OCHRE, TEAL, RUST, PLUM = "#2f55b0", "#c27a1a", "#1f8f7a", "#c0533a", "#8456b0"
PAPER, RAIL, RULE = "#fbfaf7", "#f2efe7", "#e3ded2"
TEXT, MUTED = "#1d2330", "#5b6170"
SERIF, SANS = "Georgia", "Segoe UI"
ASSET_COLORS = {"Equity": INK, "Fixed income": TEAL, "Real estate": OCHRE, "Cash reserves": RUST}

# ---------------------------------------------------------------- model
TABLES = {  # model table -> (csv / duckdb table, visible columns)
    "Month": ("dim_month", {"month", "fiscal_year", "month_label", "year"}),
    "Category": ("dim_category", {"category", "category_group"}),
    "Transactions": ("fact_transactions", {"txn_date", "description", "amount", "source"}),
    "Monthly": ("mart_monthly", set()),
    "Category Spend": ("mart_category_monthly", set()),
    "Holdings": ("fact_holdings", {"asset_class", "instrument"}),
    "Liabilities": ("fact_liabilities", {"liability"}),
    "Net Worth": ("mart_net_worth", set()),
    "Cashflow": ("mart_cashflow_sankey", {"source", "target"}),
    "FIRE Inputs": ("mart_fire_inputs", set()),
}
RELATIONSHIPS = [(t, "month", "Month", "month") for t in
                 ("Transactions", "Monthly", "Category Spend", "Holdings", "Liabilities", "Net Worth", "Cashflow")]
RELATIONSHIPS += [("Transactions", "category", "Category", "category"),
                  ("Category Spend", "category", "Category", "category")]

LAKH = '"₹"#,0.0" L"'
LAKH2 = '"₹"#,0.00" L"'
CRORE = '"₹"#,0.00" Cr"'
PCT = "0.0%"

MEASURES = [
    # name, dax, format, description
    ("Avg money in", "DIVIDE ( SUM ( Monthly[income] ), DISTINCTCOUNT ( Monthly[month] ) ) / 100000", LAKH2,
     "Average monthly take-home, bonus and other income, in lakh."),
    ("Avg spend", "DIVIDE ( SUM ( Monthly[total_spend] ), DISTINCTCOUNT ( Monthly[month] ) ) / 100000", LAKH2,
     "Average monthly fixed plus discretionary spending, in lakh."),
    ("Avg invested", "DIVIDE ( SUM ( Monthly[invested] ) - SUM ( Monthly[withdrawn] ), DISTINCTCOUNT ( Monthly[month] ) ) / 100000", LAKH2,
     "Average monthly net new money into investments, in lakh."),
    ("Savings rate", "DIVIDE ( SUM ( Monthly[income] ) - SUM ( Monthly[total_spend] ), SUM ( Monthly[income] ) )", PCT,
     "Share of money in that was not spent. Weighted across the selected months."),
    ("Savings rate (12m)", """
        VAR m = MAX ( 'Month'[month] )
        RETURN
            CALCULATE (
                [Savings rate],
                FILTER ( ALL ( 'Month' ), 'Month'[month] > EDATE ( m, -12 ) && 'Month'[month] <= m )
            )""", PCT, "Savings rate over the trailing 12 months."),
    ("Debt to income", "DIVIDE ( SUM ( Monthly[emi] ), SUM ( Monthly[gross] ) )", PCT,
     "Loan EMIs as a share of gross pay."),
    ("Net worth", """
        VAR m = MAX ( 'Month'[month] )
        RETURN CALCULATE ( SUM ( 'Net Worth'[net_worth] ), 'Month'[month] = m ) / 100000""", LAKH,
     "Assets minus liabilities at the last month in context, in lakh."),
    ("Total assets", """
        VAR m = MAX ( 'Month'[month] )
        RETURN CALCULATE ( SUM ( 'Net Worth'[total_assets] ), 'Month'[month] = m ) / 100000""", LAKH,
     "Total assets at the last month in context, in lakh."),
    ("Total liabilities", """
        VAR m = MAX ( 'Month'[month] )
        RETURN CALCULATE ( SUM ( 'Net Worth'[total_liabilities] ), 'Month'[month] = m ) / 100000""", LAKH,
     "Loans plus card balance at the last month in context, in lakh."),
    ("Net worth change", """
        VAR f = MIN ( 'Month'[month] )
        VAR first = CALCULATE ( SUM ( 'Net Worth'[net_worth] ), 'Month'[month] = f ) / 100000
        RETURN [Net worth] - first""", '"+₹"#,0.0" L";"−₹"#,0.0" L"',
     "Net worth at the end of the selection minus net worth at its start, in lakh."),
    ("Holding value", """
        VAR m = MAX ( 'Month'[month] )
        RETURN CALCULATE ( SUM ( Holdings[value] ), 'Month'[month] = m ) / 100000""", LAKH,
     "Value of holdings at the last month in context, in lakh."),
    ("Asset share", "DIVIDE ( [Holding value], CALCULATE ( [Holding value], REMOVEFILTERS ( Holdings[asset_class], Holdings[instrument] ) ) )", PCT,
     "Share of total assets."),
    ("Cash share", "CALCULATE ( [Asset share], Holdings[asset_class] = \"Cash reserves\" )", PCT,
     "Share of assets in savings account and fixed deposits."),
    ("Emergency cover", """
        DIVIDE (
            CALCULATE ( [Holding value], Holdings[asset_class] = "Cash reserves" ) * 100000,
            DIVIDE ( SUM ( Monthly[total_spend] ), DISTINCTCOUNT ( Monthly[month] ) )
        )""", '0.0" months"', "Months of average spending that cash reserves cover."),
    ("Category spend", """
        DIVIDE (
            SUM ( 'Category Spend'[spend] ),
            CALCULATE ( DISTINCTCOUNT ( Monthly[month] ), REMOVEFILTERS ( Category ) )
        ) / 1000""", '"₹"#,0.0"k"', "Average monthly spend in thousands."),
    ("Flow", "SUM ( Cashflow[amount] )", '"₹"#,0', "Rupees moving between two cashflow nodes."),
    ("Flow (summary)", "CALCULATE ( SUM ( Cashflow[amount] ), Cashflow[stage] <= 3 ) / 100000", LAKH,
     "Cashflow up to the fixed, discretionary, investment and cash split; category detail sits in the bar chart."),
    ("Return assumption", "SELECTEDVALUE ( 'Expected return'[Expected return], 10 ) / 100", PCT,
     "Nominal annual portfolio return chosen on the slider."),
    ("Inflation assumption", "SELECTEDVALUE ( Inflation[Inflation], 6 ) / 100", PCT,
     "Annual inflation chosen on the slider."),
    ("Withdrawal rate assumption", "SELECTEDVALUE ( 'Withdrawal rate'[Withdrawal rate], 3.5 ) / 100", PCT,
     "Safe withdrawal rate chosen on the slider."),
    ("Projected assets", """
        VAR y = SELECTEDVALUE ( 'FIRE Years'[Year offset] )
        VAR r = [Return assumption]
        VAR g = 0.07
        VAR a = SUM ( 'FIRE Inputs'[investable_assets] )
        VAR c = SUM ( 'FIRE Inputs'[annual_contribution] )
        VAR growth =
            IF ( ABS ( r - g ) < 0.000000001, c * y * POWER ( 1 + r, y - 1 ), c * ( POWER ( 1 + r, y ) - POWER ( 1 + g, y ) ) / ( r - g ) )
        RETURN IF ( NOT ISBLANK ( y ), ( a * POWER ( 1 + r, y ) + growth ) / 10000000 )""", CRORE,
     "Portfolio projected for a year: today's assets compounded, plus contributions growing 7% a year. Crore."),
    ("FIRE target", """
        VAR y = SELECTEDVALUE ( 'FIRE Years'[Year offset] )
        VAR s = SUM ( 'FIRE Inputs'[annual_spend] )
        RETURN IF ( NOT ISBLANK ( y ), s * POWER ( 1 + [Inflation assumption], y ) / [Withdrawal rate assumption] / 10000000 )""", CRORE,
     "Corpus needed that year: inflated annual spend divided by the withdrawal rate. Crore."),
    ("FIRE age", "MINX ( FILTER ( ALL ( 'FIRE Years' ), [Projected assets] >= [FIRE target] ), 'FIRE Years'[Age] )", "0",
     "First age at which projected assets cover the FIRE target."),
    ("Years to FIRE", "[FIRE age] - MAX ( 'FIRE Inputs'[current_age] )", '0" years"',
     "Years from today until the FIRE age."),
    ("Corpus at FIRE", """
        VAR age = [FIRE age]
        RETURN CALCULATE ( [FIRE target], FILTER ( ALL ( 'FIRE Years' ), 'FIRE Years'[Age] = age ) )""", CRORE,
     "FIRE target in the year it is reached. Crore."),
    ("FIRE progress", "DIVIDE ( SUM ( 'FIRE Inputs'[investable_assets] ), SUM ( 'FIRE Inputs'[annual_spend] ) / [Withdrawal rate assumption] )", "0%",
     "Today's investable assets as a share of today's FIRE target."),
    ("Insight: flow", """
        VAR income = SUM ( Monthly[income] )
        VAR fixedShare = DIVIDE ( SUM ( Monthly[fixed_spend] ), income )
        VAR investShare = DIVIDE ( SUM ( Monthly[invested] ) - SUM ( Monthly[withdrawn] ), income )
        VAR top2 =
            CONCATENATEX (
                TOPN ( 2, VALUES ( 'Category Spend'[category] ), CALCULATE ( SUM ( 'Category Spend'[spend] ) ) ),
                'Category Spend'[category], " and ", CALCULATE ( SUM ( 'Category Spend'[spend] ) ), DESC
            )
        RETURN
            "Fixed costs take " & FORMAT ( fixedShare, "0%" ) & " of money in and " & FORMAT ( investShare, "0%" )
                & " goes into investments. The two biggest spending lines are " & SUBSTITUTE ( LOWER ( top2 ), "emis", "EMIs" ) & "." """, None,
     "One-line reading of the cashflow page."),
    ("Insight: own", """
        VAR f = MIN ( 'Month'[month] )
        VAR first = CALCULATE ( SUM ( 'Net Worth'[net_worth] ), 'Month'[month] = f ) / 100000
        RETURN
            "Net worth moved from " & FORMAT ( first, "₹#,0.0" ) & " L to " & FORMAT ( [Net worth], "₹#,0.0" ) & " L. "
                & FORMAT ( [Cash share], "0%" ) & " of assets sits in cash and fixed deposits, "
                & FORMAT ( [Emergency cover], "0" ) & " months of spending." """, None,
     "One-line reading of the net worth page."),
    ("Insight: track", """
        "The savings rate is " & FORMAT ( [Savings rate], "0%" ) & " for this period, and loan EMIs take "
            & FORMAT ( [Debt to income], "0.0%" ) & " of gross pay. The step down in March 2023 is the car purchase." """, None,
     "One-line reading of the on-track page."),
    ("Insight: stop", """
        VAR age = [FIRE age]
        RETURN
            IF (
                ISBLANK ( age ),
                "Not reached within 30 years on these assumptions.",
                "On these assumptions, work becomes optional at " & age & ", in "
                    & ( YEAR ( MAX ( 'FIRE Inputs'[as_of] ) ) + [Years to FIRE] ) & ". Move the sliders to test it."
            )""", None, "One-line reading of the FIRE page."),
]

CALC_TABLES = {
    "Metrics": ("{ 1 }", [("Value", "int64", "[Value]", True, None)]),
    "FIRE Years": ("""
            VAR age = MAX ( 'FIRE Inputs'[current_age] )
            RETURN
                SELECTCOLUMNS ( GENERATESERIES ( 0, 30, 1 ), "Year offset", [Value], "Age", [Value] + age )""",
                   [("Year offset", "int64", "[Year offset]", True, "0"), ("Age", "int64", "[Age]", False, "0")]),
    "Expected return": ('SELECTCOLUMNS ( GENERATESERIES ( 6, 14, 0.5 ), "Expected return", [Value] )',
                        [("Expected return", "double", "[Expected return]", False, '0.0"%"')]),
    "Inflation": ('SELECTCOLUMNS ( GENERATESERIES ( 3, 9, 0.5 ), "Inflation", [Value] )',
                  [("Inflation", "double", "[Inflation]", False, '0.0"%"')]),
    "Withdrawal rate": ('SELECTCOLUMNS ( GENERATESERIES ( 2.5, 5, 0.25 ), "Withdrawal rate", [Value] )',
                        [("Withdrawal rate", "double", "[Withdrawal rate]", False, '0.00"%"')]),
}

TYPE_MAP = {"DATE": ("dateTime", "type date", "Short Date"), "VARCHAR": ("string", "type text", None),
            "BIGINT": ("int64", "Int64.Type", "0"), "INTEGER": ("int64", "Int64.Type", "0"),
            "DOUBLE": ("double", "type number", "#,0.00"), "BOOLEAN": ("boolean", "type logical", None)}


def fmt_value(fmt):
    """TMDL needs values containing quotes wrapped in quotes, with inner quotes doubled."""
    return '"' + fmt.replace('"', '""') + '"' if '"' in fmt else fmt


def q(name):
    return f"'{name}'" if any(c in name for c in " .=:'-()") else name


def tmdl_type(duck_type):
    if duck_type.startswith("DECIMAL"):
        return TYPE_MAP["DOUBLE"]
    return TYPE_MAP[duck_type]


def indent(text, tabs):
    lines = [l for l in text.strip("\n").splitlines()]
    pad = min(len(l) - len(l.lstrip()) for l in lines if l.strip())
    return "\n".join("\t" * tabs + l[pad:] for l in lines)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path, obj):
    write(path, json.dumps(obj, indent=2, ensure_ascii=False))


def build_model(con):
    d = SM / "definition"
    write_json(SM / "definition.pbism", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {}})
    write_json(SM / ".platform", platform("SemanticModel"))
    write(d / "database.tmdl", "database\n\tcompatibilityLevel: 1601\n")
    refs = "\n".join(f"ref table {q(t)}" for t in list(TABLES) + list(CALC_TABLES))
    write(d / "model.tmdl", f"""model Model
\tculture: en-US
\tdefaultPowerBIDataSourceVersion: powerBI_V3
\tdiscourageImplicitMeasures
\tsourceQueryCulture: en-US
\tdataAccessOptions
\t\tlegacyRedirects
\t\treturnErrorValuesAsNull

annotation __PBI_TimeIntelligenceEnabled = 0

annotation PBI_ProTooling = ["TMDL-Extension"]

{refs}
""")
    write(d / "expressions.tmdl",
          f'expression DataFolder = "{DATA_FOLDER}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n')

    for table, (source, visible) in TABLES.items():
        cols = con.sql(f"DESCRIBE {source}").fetchall()
        out = [f"table {q(table)}", ""]
        for col, ctype, *_ in cols:
            dtype, _, fmt = tmdl_type(ctype)
            out.append(f"\tcolumn {q(col)}")
            out.append(f"\t\tdataType: {dtype}")
            if fmt:
                out.append(f"\t\tformatString: {fmt_value(fmt)}")
            if col not in visible:
                out.append("\t\tisHidden")
            if table == "Month" and col == "month_label":
                out.append("\t\tsortByColumn: month_index")
            out.append("\t\tsummarizeBy: none")
            out.append(f"\t\tsourceColumn: {col}")
            out.append("")
        types = ",\n".join(f'\t\t\t\t\t\t{{"{c}", {tmdl_type(t)[1]}}}' for c, t, *_ in cols)
        out.append(f"\tpartition {q(table)} = m")
        out.append("\t\tmode: import")
        out.append("\t\tsource =")
        out.append(f"""\t\t\t\tlet
\t\t\t\t    Source = Csv.Document(File.Contents(DataFolder & "{source}.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
\t\t\t\t    #"Promoted headers" = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
\t\t\t\t    #"Typed columns" = Table.TransformColumnTypes(
\t\t\t\t        #"Promoted headers",
\t\t\t\t        {{
{types}
\t\t\t\t        }},
\t\t\t\t        "en-US"
\t\t\t\t    )
\t\t\t\tin
\t\t\t\t    #"Typed columns"
""")
        write(d / "tables" / f"{table}.tmdl", "\n".join(out))

    for table, (dax, cols) in CALC_TABLES.items():
        out = [f"table {q(table)}", ""]
        if table == "Metrics":
            for name, expr, fmt, desc in MEASURES:
                out.append(f"\t/// {desc}")
                if "\n" in expr.strip():
                    out.append(f"\tmeasure {q(name)} = ```")
                    out.append(indent(expr, 3))
                    out.append("\t\t\t```")
                else:
                    out.append(f"\tmeasure {q(name)} = {expr.strip()}")
                if fmt:
                    out.append(f"\t\tformatString: {fmt_value(fmt)}")
                out.append("")
        for col, dtype, src, hidden, fmt in cols:
            out.append(f"\tcolumn {q(col)}")
            out.append(f"\t\tdataType: {dtype}")
            if fmt:
                out.append(f"\t\tformatString: {fmt_value(fmt)}")
            if hidden:
                out.append("\t\tisHidden")
            out.append("\t\tsummarizeBy: none")
            out.append(f"\t\tsourceColumn: {src}")
            out.append("")
        out.append(f"\tpartition {q(table)} = calculated")
        out.append("\t\tmode: import")
        if "\n" in dax.strip():
            out.append("\t\tsource = ```")
            out.append(indent(dax, 4))
            out.append("\t\t\t\t```")
        else:
            out.append(f"\t\tsource = {dax}")
        out.append("")
        write(d / "tables" / f"{table}.tmdl", "\n".join(out))

    rels = []
    for ft, fc, tt, tc in RELATIONSHIPS:
        rid = uuid.uuid5(uuid.NAMESPACE_URL, f"{ft}.{fc}->{tt}.{tc}")
        rels.append(f"relationship {rid}\n\tfromColumn: {q(ft)}.{q(fc)}\n\ttoColumn: {q(tt)}.{q(tc)}\n")
    write(d / "relationships.tmdl", "\n".join(rels))


# ---------------------------------------------------------------- report helpers
def uid(*parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def platform(kind):
    return {"$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": kind, "displayName": NAME},
            "config": {"version": "2.0", "logicalId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{NAME}-{kind}"))}}


def lit(v):
    return {"expr": {"Literal": {"Value": v}}}


def s(text):
    return lit("'" + text.replace("'", "''") + "'")


def num(v, suffix="D"):
    return lit(f"{v}{suffix}")


def color(hex_):
    return {"solid": {"color": s(hex_)}}


def measure(name, entity="Metrics"):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": name}}


def column(entity, name):
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": name}}


def proj(field, display=None):
    kind = "Measure" if "Measure" in field else "Column"
    ent = field[kind]["Expression"]["SourceRef"]["Entity"]
    prop = field[kind]["Property"]
    p = {"field": field, "queryRef": f"{ent}.{prop}", "nativeQueryRef": prop}
    if kind == "Column":
        p["active"] = True
    if display:
        p["displayName"] = display
    return p


def container(title=None, bg=None, pad=None):
    c = {"title": [{"properties": {"show": lit("true" if title else "false")}}],
         "background": [{"properties": {"show": lit("true" if bg else "false")}}],
         "border": [{"properties": {"show": lit("false")}}],
         "dropShadow": [{"properties": {"show": lit("false")}}],
         "visualHeader": [{"properties": {"show": lit("false")}}]}
    if title:
        c["title"][0]["properties"].update({"text": s(title), "fontFamily": s(SANS), "fontSize": num(11),
                                            "fontColor": color(MUTED), "bold": lit("false")})
    if bg:
        c["background"][0]["properties"].update({"color": color(bg), "transparency": num(0)})
    if pad is not None:
        c["padding"] = [{"properties": {k: num(pad) for k in ("top", "bottom", "left", "right")}}]
    return c


class Page:
    def __init__(self, key, display):
        self.key, self.display, self.visuals = key, display, []

    def add(self, vid, x, y, w, h, visual, z=None):
        self.visuals.append({
            "$schema": f"{SCHEMA}/visualContainer/2.1.0/schema.json",
            "name": uid(self.key, vid),
            "position": {"x": x, "y": y, "z": z if z is not None else 1000 * (len(self.visuals) + 1),
                         "height": h, "width": w, "tabOrder": 1000 * (len(self.visuals) + 1)},
            "visual": visual})
        self.visuals[-1]["_folder"] = vid


def textbox(paragraphs):
    return {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]},
            "visualContainerObjects": container(), "drillFilterOtherVisuals": True}


def run(text, font=SANS, size=10, col=TEXT, bold=False):
    style = {"fontFamily": font, "fontSize": f"{size}pt", "color": col}
    if bold:
        style["fontWeight"] = "bold"
    return {"value": text, "textStyle": style}


def kpi_cards(fields):
    return {"visualType": "cardVisual",
            "query": {"queryState": {"Data": {"projections": [proj(measure(m), d) for m, d in fields]}}},
            "objects": {
                "layout": [{"properties": {"orientation": num(0), "columnCount": num(len(fields), "L"),
                                           "style": s("Cards"), "alignment": s("left")}}],
                "value": [{"properties": {"fontColor": color(TEXT), "fontSize": num(24), "fontFamily": s(SERIF)},
                           "selector": {"id": "default"}}],
                "label": [{"properties": {"fontColor": color(MUTED), "fontSize": num(10), "fontFamily": s(SANS),
                                          "position": s("aboveValue")}, "selector": {"id": "default"}}],
                "fillCustom": [{"properties": {"show": lit("false")}, "selector": {"id": "default"}}],
                "outline": [{"properties": {"show": lit("false")}, "selector": {"id": "default"}}],
                "accentBar": [{"properties": {"show": lit("true"), "position": s("Left"), "color": color(RULE),
                                              "width": num(2)}, "selector": {"id": "default"}}],
                "shadowCustom": [{"properties": {"show": lit("false")}, "selector": {"id": "default"}}],
                "padding": [{"properties": {"paddingSelection": s("Narrow")}, "selector": {"id": "default"}}]},
            "visualContainerObjects": container(), "drillFilterOtherVisuals": True}


def insight(measure_name):
    return {"visualType": "cardVisual",
            "query": {"queryState": {"Data": {"projections": [proj(measure(measure_name))]}}},
            "objects": {
                "layout": [{"properties": {"orientation": num(0), "columnCount": num(1, "L"), "alignment": s("left")}}],
                "value": [{"properties": {"fontColor": color(MUTED), "fontSize": num(12), "fontFamily": s(SANS),
                                          "horizontalAlignment": s("left")}, "selector": {"id": "default"}}],
                "label": [{"properties": {"show": lit("false")}, "selector": {"id": "default"}}],
                "fillCustom": [{"properties": {"show": lit("false")}, "selector": {"id": "default"}}],
                "outline": [{"properties": {"show": lit("false")}, "selector": {"id": "default"}}],
                "accentBar": [{"properties": {"show": lit("false")}, "selector": {"id": "default"}}],
                "shadowCustom": [{"properties": {"show": lit("false")}, "selector": {"id": "default"}}],
                "padding": [{"properties": {"paddingSelection": s("Narrow")}, "selector": {"id": "default"}}]},
            "visualContainerObjects": container(), "drillFilterOtherVisuals": True}


def axis_objects(percent=False):
    return {"categoryAxis": [{"properties": {"labelColor": color(MUTED), "fontSize": num(9), "showAxisTitle": lit("false"),
                                             "gridlineShow": lit("false")}}],
            "valueAxis": [{"properties": {"labelColor": color(MUTED), "fontSize": num(9), "showAxisTitle": lit("false"),
                                          "gridlineColor": color(RULE), "gridlineThickness": num(1, "L")}}],
            "legend": [{"properties": {"show": lit("true"), "position": s("Top"), "labelColor": color(TEXT),
                                       "fontSize": num(9), "showTitle": lit("false")}}]}


def series_colors(entity, col, mapping):
    out = []
    for value, hex_ in mapping.items():
        out.append({"properties": {"fill": color(hex_)},
                    "selector": {"data": [{"scopeId": {"Comparison": {
                        "ComparisonKind": 0, "Left": column(entity, col), "Right": {"Literal": {"Value": f"'{value}'"}}}}}]}})
    return out


def measure_colors(mapping):
    return [{"properties": {"fill": color(hex_)}, "selector": {"metadata": f"Metrics.{m}"}} for m, hex_ in mapping.items()]


def line_colors(mapping):
    return [{"properties": {"strokeWidth": num(2, "L"), "showMarker": lit("false")}, "selector": {"metadata": f"Metrics.{m}"}}
            for m in mapping] + measure_colors(mapping)


SANKEY_SOURCES = {"Gross pay": INK, "Take-home pay": INK, "Bonus": INK, "Other income": INK, "Money in": INK,
                  "Savings withdrawn": "#9aa1b0", "Cash drawn down": "#9aa1b0"}
SANKEY_TARGETS = {("Gross pay", "Income tax"): "#9aa1b0", ("Gross pay", "Provident fund"): TEAL,
                  ("Gross pay", "Professional tax"): "#9aa1b0", ("Money in", "Fixed costs"): RUST,
                  ("Money in", "Discretionary"): OCHRE, ("Money in", "Investments"): TEAL,
                  ("Money in", "Cash saved"): TEAL}


def eq(entity, col, value):
    return {"Comparison": {"ComparisonKind": 0, "Left": column(entity, col), "Right": {"Literal": {"Value": f"'{value}'"}}}}


def sankey_node_colors():
    """The Sankey colours nodes one by one: a source by its value, a destination by its (source, target) pair."""
    out = series_colors("Cashflow", "source", SANKEY_SOURCES)
    for (src, tgt), hex_ in SANKEY_TARGETS.items():
        out.append({"properties": {"fill": color(hex_)},
                    "selector": {"data": [{"scopeId": eq("Cashflow", "source", src)},
                                          {"scopeId": eq("Cashflow", "target", tgt)}]}})
    return out


def chart(vtype, category, values, title, series=None, extra=None, sort_desc=None):
    qs = {"Category": {"projections": [proj(category)]},
          "Y": {"projections": [proj(measure(v)) for v in values]}}
    if series:
        qs["Series"] = {"projections": [proj(series)]}
    v = {"visualType": vtype, "query": {"queryState": qs}, "objects": axis_objects(),
         "visualContainerObjects": container(title), "drillFilterOtherVisuals": True}
    if sort_desc:
        v["query"]["sortDefinition"] = {"sort": [{"field": measure(sort_desc), "direction": "Descending"}],
                                        "isDefaultSort": False}
    if extra:
        for k, val in extra.items():
            v["objects"][k] = val
    return v


def slicer(entity, col, title, mode, sync=None, default=None):
    v = {"visualType": "slicer",
         "query": {"queryState": {"Values": {"projections": [proj(column(entity, col))]}}},
         "objects": {"data": [{"properties": {"mode": s(mode)}}],
                     "header": [{"properties": {"show": lit("true"), "text": s(title), "fontColor": color(MUTED),
                                                "fontFamily": s(SANS), "textSize": num(10)}}],
                     "items": [{"properties": {"fontColor": color(TEXT), "fontFamily": s(SANS), "textSize": num(11)}}]},
         "visualContainerObjects": container(), "drillFilterOtherVisuals": True}
    if default is not None:
        v["objects"]["data"][0]["properties"]["numericStart"] = num(default)
        v["objects"]["general"] = [{"properties": {"filter": {"filter": {
            "Version": 2, "From": [{"Name": "p", "Entity": entity, "Type": 0}],
            "Where": [{"Condition": {"Comparison": {
                "ComparisonKind": 0,
                "Left": {"Column": {"Expression": {"SourceRef": {"Source": "p"}}, "Property": col}},
                "Right": {"Literal": {"Value": f"{default}D"}}}}}]}}}}]
    if sync:
        v["syncGroup"] = {"groupName": sync, "fieldChanges": True, "filterChanges": True}
    return v


# ---------------------------------------------------------------- report pages
QUESTIONS = [("where", "Where does it go?"), ("own", "What do I own?"), ("track", "Am I on track?"),
             ("stop", "When can I stop?")]
CX, CW = 272, 976          # content column
HERO_W, SIDE_X, SIDE_W = 640, 928, 320
TOP = 226


def rail(page, with_period=True):
    backdrop = textbox([{"textRuns": [run(" ")]}])
    backdrop["visualContainerObjects"] = container(bg=RAIL)
    page.add("railBg", 0, 0, 248, 720, backdrop, z=0)
    page.add("railTitle", 24, 28, 208, 76, textbox([
        {"textRuns": [run("Personal finance", SERIF, 18, TEXT)]},
        {"textRuns": [run("Oct 2021 to Sep 2026", SANS, 9, MUTED)]}]))
    def state(sel, col, fill, bar):
        return {"text": {"properties": {"fontFamily": s(SERIF), "fontSize": num(13), "fontColor": color(col),
                                         "horizontalAlignment": s("left"), "leftMargin": num(14, "L")},
                          "selector": {"id": sel}},
                "fill": {"properties": {"show": lit("true"), "fillColor": color(fill), "transparency": num(0)},
                         "selector": {"id": sel}},
                "accentBar": {"properties": {"show": lit(bar), "position": s("Left"), "color": color(INK),
                                              "width": num(3, "L")}, "selector": {"id": sel}}}
    states = [state("default", MUTED, RAIL, "false"), state("hover", TEXT, "#ebe7dc", "false"),
              state("press", TEXT, "#ebe7dc", "false"), state("selected", INK, PAPER, "true")]
    nav_objects = {k: [st[k] for st in states] for k in ("text", "fill", "accentBar")}
    nav_objects["outline"] = [{"properties": {"show": lit("false")}}]
    nav_objects["layout"] = [{"properties": {"orientation": num(1)}}]
    page.add("railNav", 16, 116, 216, 200, {"visualType": "pageNavigator", "objects": nav_objects,
             "visualContainerObjects": container(), "drillFilterOtherVisuals": True})
    if with_period:
        page.add("railPeriod", 24, 540, 200, 80, slicer("Month", "fiscal_year", "Period", "Dropdown", sync="period"))
    page.add("railFoot", 24, 640, 208, 56, textbox([
        {"textRuns": [run("Synthetic persona, Hyderabad.", SANS, 8, MUTED)]},
        {"textRuns": [run("Python, DuckDB, Power BI.", SANS, 8, MUTED)]}]))


def header(page, question, insight_measure, kpis):
    page.add("heading", CX, 22, CW, 46, textbox([{"textRuns": [run(question, SERIF, 24, TEXT)]}]))
    page.add("insight", CX, 66, CW, 44, insight(insight_measure))
    page.add("kpis", CX, 116, CW, 96, kpi_cards(kpis))


def build_pages():
    pages = []

    p = Page("where", "Where does it go?")
    rail(p)
    header(p, p.display, "Insight: flow", [("Avg money in", "Money in per month"), ("Avg spend", "Spent per month"),
                                           ("Avg invested", "Invested per month"), ("Savings rate", "Savings rate")])
    p.add("sankey", CX, TOP, HERO_W, 470, {
        "visualType": "sankey02300D1BE6F5427989F3DE31CCA9E0F32020",
        "query": {"queryState": {"Source": {"projections": [proj(column("Cashflow", "source"), "From")]},
                                 "Destination": {"projections": [proj(column("Cashflow", "target"), "To")]},
                                 "Weight": {"projections": [proj(measure("Flow (summary)"), "Amount")]}}},
        "objects": {"nodes": [{"properties": {"nodesWidth": num(10, "L")}}] + sankey_node_colors(),
                    "links": [{"properties": {"matchNodeColors": lit("false"), "fill": color("#d9d4c7")}}],
                    "labels": [{"properties": {"show": lit("true"), "fill": color(TEXT), "fontFamily": s(SANS),
                                               "fontSize": num(10)}}]},
        "visualContainerObjects": container("Where each rupee of pay goes"),
        "drillFilterOtherVisuals": True})
    p.add("categories", SIDE_X, TOP, SIDE_W, 470, chart(
        "barChart", column("Category Spend", "category"), ["Category spend"], "Average monthly spend by category",
        sort_desc="Category spend",
        extra={"dataPoint": [{"properties": {"fill": color(INK)}}],
               "categoryAxis": [{"properties": {"labelColor": color(MUTED), "fontSize": num(9), "showAxisTitle": lit("false"),
                                                "maxMarginFactor": num(45, "L")}}],
               "valueAxis": [{"properties": {"show": lit("false"), "showAxisTitle": lit("false")}}],
               "labels": [{"properties": {"show": lit("true"), "color": color(MUTED), "fontSize": num(9)}}],
               "legend": [{"properties": {"show": lit("false")}}]}))
    pages.append(p)

    p = Page("own", "What do I own?")
    rail(p)
    header(p, p.display, "Insight: own", [("Net worth", "Net worth"), ("Total assets", "Assets"),
                                          ("Total liabilities", "Loans and card"), ("Net worth change", "Change in period")])
    p.add("assetsArea", CX, TOP, HERO_W, 470, chart(
        "stackedAreaChart", column("Month", "month"), ["Holding value"], "Assets by class, month end",
        series=column("Holdings", "asset_class"),
        extra={"dataPoint": series_colors("Holdings", "asset_class", ASSET_COLORS)}))
    p.add("allocation", SIDE_X, TOP, SIDE_W, 470, chart(
        "barChart", column("Holdings", "asset_class"), ["Asset share"], "Allocation at the end of the period",
        sort_desc="Asset share",
        extra={"dataPoint": series_colors("Holdings", "asset_class", ASSET_COLORS),
               "labels": [{"properties": {"show": lit("true"), "color": color(TEXT), "fontSize": num(10)}}],
               "valueAxis": [{"properties": {"show": lit("false"), "showAxisTitle": lit("false")}}],
               "categoryAxis": [{"properties": {"labelColor": color(MUTED), "fontSize": num(10), "showAxisTitle": lit("false"),
                                                "maxMarginFactor": num(45, "L")}}],
               "legend": [{"properties": {"show": lit("false")}}]}))
    pages.append(p)

    p = Page("track", "Am I on track?")
    rail(p)
    header(p, p.display, "Insight: track", [("Savings rate", "Savings rate"), ("Debt to income", "Debt to income"),
                                            ("Emergency cover", "Emergency cover"), ("Net worth change", "Net worth change")])
    p.add("savingsLine", CX, TOP, HERO_W, 470, chart(
        "lineChart", column("Month", "month"), ["Savings rate (12m)"], "Savings rate, trailing 12 months",
        extra={"dataPoint": measure_colors({"Savings rate (12m)": INK}),
               "lineStyles": line_colors({"Savings rate (12m)": INK}),
               "legend": [{"properties": {"show": lit("false")}}],
               "valueAxis": [{"properties": {"labelColor": color(MUTED), "fontSize": num(9), "showAxisTitle": lit("false"),
                                             "gridlineColor": color(RULE), "start": num(0)}}]}))
    p.add("dtiLine", SIDE_X, TOP, SIDE_W, 470, chart(
        "lineChart", column("Month", "month"), ["Debt to income"], "Debt to income",
        extra={"dataPoint": measure_colors({"Debt to income": RUST}),
               "lineStyles": line_colors({"Debt to income": RUST}),
               "valueAxis": [{"properties": {"labelColor": color(MUTED), "fontSize": num(9), "showAxisTitle": lit("false"),
                                             "gridlineColor": color(RULE), "start": num(0)}}],
               "legend": [{"properties": {"show": lit("false")}}]}))
    pages.append(p)

    p = Page("stop", "When can I stop?")
    rail(p, with_period=False)
    header(p, p.display, "Insight: stop", [("FIRE age", "FIRE age"), ("Years to FIRE", "Years to go"),
                                           ("Corpus at FIRE", "Corpus needed then"), ("FIRE progress", "Progress today")])
    p.add("fireLine", CX, TOP, HERO_W, 470, chart(
        "lineChart", column("FIRE Years", "Age"), ["Projected assets", "FIRE target"], "Projected portfolio against the FIRE target, by age",
        extra={"dataPoint": measure_colors({"Projected assets": INK, "FIRE target": OCHRE}),
               "lineStyles": line_colors({"Projected assets": INK, "FIRE target": OCHRE})}))
    for i, (tbl, title, default) in enumerate([("Expected return", "Expected return, % a year", 10),
                                               ("Inflation", "Inflation, % a year", 6),
                                               ("Withdrawal rate", "Safe withdrawal rate, %", 3.5)]):
        p.add(f"param{i}", SIDE_X, TOP + 8 + i * 118, SIDE_W, 104, slicer(tbl, tbl, title, "Single", default=default))
    p.add("fireNote", SIDE_X, TOP + 370, SIDE_W, 96, textbox([
        {"textRuns": [run("Contributions grow 7% a year. Spending excludes loan EMIs, which end before retirement. "
                          "Defaults: 10% return, 6% inflation, 3.5% withdrawal.", SANS, 9, MUTED)]}]))
    pages.append(p)
    return pages


def theme():
    return {
        "name": "Editorial ledger",
        "dataColors": [INK, OCHRE, TEAL, RUST, PLUM, "#5e6676", "#9aa6c8", "#c9b99a"],
        "background": PAPER, "foreground": TEXT, "tableAccent": INK,
        "good": TEAL, "neutral": OCHRE, "bad": RUST,
        "textClasses": {
            "callout": {"fontFace": SERIF, "fontSize": 24, "color": TEXT},
            "title": {"fontFace": SANS, "fontSize": 11, "color": MUTED},
            "header": {"fontFace": SANS, "fontSize": 11, "color": TEXT},
            "label": {"fontFace": SANS, "fontSize": 10, "color": MUTED}},
        "visualStyles": {
            "page": {"*": {"background": [{"color": {"solid": {"color": PAPER}}, "transparency": 0}],
                           "outspace": [{"color": {"solid": {"color": PAPER}}}]}},
            "*": {"*": {"background": [{"show": False}], "border": [{"show": False}],
                        "dropShadow": [{"show": False}]}}},
    }


def build_report():
    d = RP / "definition"
    write_json(RP / ".platform", platform("Report"))
    write_json(RP / "definition.pbir", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    write_json(d / "version.json", {"$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    write_json(RP / "StaticResources" / "RegisteredResources" / "theme.json", theme())
    write_json(d / "report.json", {
        "$schema": f"{SCHEMA}/report/3.0.0/schema.json",
        "themeCollection": {
            "baseTheme": {"name": "CY24SU10", "reportVersionAtImport": {"visual": "1.8.97", "report": "2.0.97", "page": "1.3.97"},
                          "type": "SharedResources"},
            "customTheme": {"name": "theme.json", "reportVersionAtImport": {"visual": "1.8.100", "report": "2.0.100", "page": "1.3.100"},
                            "type": "RegisteredResources"}},
        "publicCustomVisuals": ["sankey02300D1BE6F5427989F3DE31CCA9E0F32020"],
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": "theme.json", "path": "theme.json", "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "defaultDrillFilterOtherVisuals": True,
                     "useEnhancedTooltips": True, "hideVisualContainerHeader": True}})
    pages = build_pages()
    write_json(d / "pages" / "pages.json", {
        "$schema": f"{SCHEMA}/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [p.key for p in pages], "activePageName": pages[0].key})
    for p in pages:
        write_json(d / "pages" / p.key / "page.json", {
            "$schema": f"{SCHEMA}/page/1.4.0/schema.json", "name": p.key, "displayName": p.display,
            "displayOption": "FitToPage", "height": 720, "width": 1280})
        for v in p.visuals:
            folder = v.pop("_folder")
            write_json(d / "pages" / p.key / "visuals" / folder / "visual.json", v)


def main():
    global DATA_FOLDER
    parser = argparse.ArgumentParser(description="Generate the Power BI project.")
    parser.add_argument("--portable", action="store_true",
                        help="use the neutral path C:\\personal-finance-bi\\data\\model instead of this checkout's path")
    if parser.parse_args().portable:
        DATA_FOLDER = PORTABLE_DATA_FOLDER
    for path in (SM, RP):
        if path.exists():
            shutil.rmtree(path)
    con = duckdb.connect(str(ROOT / "data" / "finance.duckdb"), read_only=True)
    build_model(con)
    build_report()
    write_json(OUT / f"{NAME}.pbip", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}], "settings": {"enableAutoRecovery": True}})
    write(OUT / ".gitignore", "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n")
    print(f"wrote {OUT / (NAME + '.pbip')}")


if __name__ == "__main__":
    main()
