from pathlib import Path
import re

OLD_VERSION = "0.3.333"
NEW_VERSION = "0.3.334"

index_path = Path("static/index.html")
main_path = Path("app/main.py")

index = index_path.read_text(encoding="utf-8")
main = main_path.read_text(encoding="utf-8")

style_pattern = re.compile(r'<style id="um-card-resource-port-v0333">.*?</style>', re.S)
style_replacement = r'''<style id="um-card-resource-port-v0334">
/* v0.3.334 — fixed three-column card geometry; CPU/RAM matches the supplied reference. */
.update-card-summary > .update-card-stats{
  height:94px !important;
  min-height:94px !important;
  max-height:94px !important;
  display:grid !important;
  grid-template-columns:minmax(86px,.92fr) minmax(144px,1.5fr) minmax(86px,.92fr) !important;
  align-items:stretch !important;
}

.update-card-summary > .update-card-stats > .update-card-stat{
  position:relative !important;
  min-width:0 !important;
  min-height:74px !important;
  box-sizing:border-box !important;
}

/* Container: explicit semantic cube icon. Never inherit the old positional icon. */
.update-card-summary > .update-card-stats > .update-card-stat:first-child{
  --status-band-icon:url("data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22black%22%20stroke-width%3D%221.7%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpath%20d%3D%22M4%207l8-4%208%204-8%204-8-4Z%22%2F%3E%3Cpath%20d%3D%22M4%207v10l8%204%208-4V7M12%2011v10%22%2F%3E%3C%2Fsvg%3E") !important;
  padding-left:8px !important;
  padding-right:8px !important;
  overflow:hidden !important;
}
.update-card-summary > .update-card-stats > .update-card-stat:first-child::before{
  left:9px !important;
  top:43px !important;
  width:21px !important;
  height:21px !important;
  transform:none !important;
  background:#69adff !important;
}
.update-card-summary > .update-card-stats > .update-card-stat:first-child .update-card-stat-label{
  width:100% !important;
  margin:0 0 5px !important;
  text-align:center !important;
  white-space:nowrap !important;
}
.update-card-summary > .update-card-stats > .update-card-stat:first-child .update-card-stat-value{
  width:100% !important;
  min-width:0 !important;
  padding-left:30px !important;
  padding-right:0 !important;
  text-align:left !important;
  white-space:nowrap !important;
}

/* CPU / RAM: the chip belongs only to this center column. */
.update-card-summary > .update-card-stats > .update-card-stat-resource{
  --status-band-icon:url("data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22black%22%20stroke-width%3D%221.7%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Crect%20x%3D%227%22%20y%3D%227%22%20width%3D%2210%22%20height%3D%2210%22%20rx%3D%222%22%2F%3E%3Cpath%20d%3D%22M9%201v3m6-3v3M9%2020v3m6-3v3M20%209h3m-3%206h3M1%209h3m-3%206h3%22%2F%3E%3C%2Fsvg%3E") !important;
  display:grid !important;
  grid-template-columns:27px minmax(0,1fr) !important;
  grid-template-rows:27px minmax(0,1fr) !important;
  column-gap:6px !important;
  row-gap:2px !important;
  align-content:center !important;
  padding:8px 8px 7px !important;
  overflow:visible !important;
}
.update-card-summary > .update-card-stats > .update-card-stat-resource::before{
  position:relative !important;
  left:auto !important;
  top:auto !important;
  grid-column:1 !important;
  grid-row:1 !important;
  align-self:center !important;
  justify-self:center !important;
  width:24px !important;
  height:24px !important;
  transform:none !important;
  background:#69adff !important;
}
.update-card-stat-resource .update-resource-title{
  grid-column:2 !important;
  grid-row:1 !important;
  align-self:center !important;
  min-width:0 !important;
  min-height:0 !important;
  margin:0 !important;
  padding:0 !important;
  display:block !important;
  color:#69adff !important;
  font-size:12px !important;
  font-weight:500 !important;
  line-height:1.05 !important;
  text-align:left !important;
  white-space:nowrap !important;
}
.update-resource-bars{
  grid-column:1 / -1 !important;
  grid-row:2 !important;
  align-self:center !important;
  width:100% !important;
  min-width:0 !important;
  display:flex !important;
  flex-direction:column !important;
  gap:7px !important;
}
.update-resource-row{
  position:relative !important;
  width:100% !important;
  min-width:0 !important;
  min-height:13px !important;
  display:grid !important;
  grid-template-columns:29px minmax(44px,1fr) 34px !important;
  align-items:center !important;
  column-gap:5px !important;
}
.update-resource-name{
  min-width:0 !important;
  color:#72afff !important;
  font-size:11px !important;
  font-weight:500 !important;
  line-height:1 !important;
  white-space:nowrap !important;
}
.update-resource-track{
  display:block !important;
  width:100% !important;
  min-width:0 !important;
  height:13px !important;
  overflow:hidden !important;
  border-radius:4px !important;
  background:#22313e !important;
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.015) !important;
}
.update-resource-fill{
  display:block !important;
  height:100% !important;
  min-width:0 !important;
  border-radius:inherit !important;
  background:linear-gradient(90deg,#6eaaff 0%,#76b7ff 100%) !important;
  transition:width .22s ease !important;
}
.update-resource-percent{
  width:34px !important;
  min-width:34px !important;
  max-width:34px !important;
  overflow:hidden !important;
  color:#d5e0e8 !important;
  font-size:10px !important;
  font-weight:500 !important;
  line-height:1 !important;
  text-align:right !important;
  white-space:nowrap !important;
}
.update-resource-ram-row{outline:none !important;}
.update-resource-tooltip{
  position:absolute !important;
  z-index:50 !important;
  left:34px !important;
  top:20px !important;
  min-width:151px !important;
  padding:7px 9px 8px !important;
  display:flex !important;
  flex-direction:column !important;
  gap:3px !important;
  border:1px solid #39444d !important;
  border-radius:5px !important;
  background:#1d2329 !important;
  box-shadow:0 5px 14px rgba(0,0,0,.34) !important;
  color:#e7edf2 !important;
  font-size:10.5px !important;
  line-height:1.2 !important;
  white-space:nowrap !important;
  pointer-events:none !important;
  opacity:0 !important;
  visibility:hidden !important;
  transform:translateY(2px) !important;
  transition:opacity .1s ease,transform .1s ease,visibility .1s ease !important;
}
.update-resource-tooltip strong{
  color:#f0f4f7 !important;
  font-size:11px !important;
  font-weight:700 !important;
}
.update-resource-tooltip[hidden]{display:none !important;}
.update-resource-ram-row:hover .update-resource-tooltip,
.update-resource-ram-row:focus .update-resource-tooltip,
.update-resource-ram-row:focus-within .update-resource-tooltip{
  opacity:1 !important;
  visibility:visible !important;
  transform:translateY(0) !important;
}

/* Port: explicit connector icon and a reserved two-line value area. */
.update-card-summary > .update-card-stats > .update-card-stat-port{
  --status-band-icon:url("data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22black%22%20stroke-width%3D%221.7%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Crect%20x%3D%224%22%20y%3D%225%22%20width%3D%2216%22%20height%3D%2214%22%20rx%3D%222%22%2F%3E%3Cpath%20d%3D%22M8%205v5h8V5M8%2014h2m2%200h2m2%200h1%22%2F%3E%3C%2Fsvg%3E") !important;
  display:flex !important;
  flex-direction:column !important;
  align-items:stretch !important;
  justify-content:flex-start !important;
  padding:8px 8px 7px !important;
  overflow:hidden !important;
  text-align:center !important;
}
.update-card-summary > .update-card-stats > .update-card-stat-port::before{
  left:8px !important;
  top:43px !important;
  width:21px !important;
  height:21px !important;
  transform:none !important;
  background:#69adff !important;
}
.update-card-stat-port .update-card-stat-label{
  width:100% !important;
  margin:0 0 5px !important;
  text-align:center !important;
  white-space:nowrap !important;
}
.update-card-stat-port .update-card-stat-value{
  width:100% !important;
  min-width:0 !important;
  min-height:28px !important;
  padding-left:27px !important;
  padding-right:0 !important;
  box-sizing:border-box !important;
  text-align:center !important;
}
.update-port-stat{
  display:grid !important;
  grid-template-rows:13px 13px !important;
  align-content:start !important;
  gap:1px !important;
  min-height:27px !important;
  max-height:27px !important;
  max-width:100% !important;
  overflow:hidden !important;
  white-space:normal !important;
  line-height:1.05 !important;
}
.update-port-line{
  display:block !important;
  min-width:0 !important;
  min-height:13px !important;
  max-width:100% !important;
  overflow:hidden !important;
  text-overflow:ellipsis !important;
  white-space:nowrap !important;
}

/* Hard isolation: values from one column can never paint into a neighbour. */
.update-card-summary > .update-card-stats > .update-card-stat:first-child,
.update-card-summary > .update-card-stats > .update-card-stat-port{
  contain:layout paint !important;
}
</style>'''

index, count = style_pattern.subn(style_replacement, index, count=1)
if count != 1:
    raise SystemExit(f"card style patch count={count}, expected 1")

if OLD_VERSION not in index:
    raise SystemExit(f"{OLD_VERSION} not found in index.html")
index = index.replace(OLD_VERSION, NEW_VERSION)

old_main = f'VERSION = "{OLD_VERSION}"'
new_main = f'VERSION = "{NEW_VERSION}"'
if main.count(old_main) != 1:
    raise SystemExit(f"main.py VERSION patch count={main.count(old_main)}, expected 1")
main = main.replace(old_main, new_main, 1)

# Validation of the exact layout guarantees introduced by this patch.
checks = [
    'id="um-card-resource-port-v0334"',
    'grid-template-columns:minmax(86px,.92fr) minmax(144px,1.5fr) minmax(86px,.92fr)',
    'grid-template-columns:29px minmax(44px,1fr) 34px',
    '.update-card-stat-port{',
    'contain:layout paint',
]
for needle in checks:
    if needle not in index:
        raise SystemExit(f"layout validation missing: {needle}")
if 'id="um-card-resource-port-v0333"' in index:
    raise SystemExit("old v0.3.333 card style still present")

index_path.write_text(index, encoding="utf-8")
main_path.write_text(main, encoding="utf-8")
print(f"Patched Update Monitor to {NEW_VERSION}")
