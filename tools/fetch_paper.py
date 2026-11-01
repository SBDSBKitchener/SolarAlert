"""Diagnostic: try to download the open-access Boteler (2019) paper and extract its text. Output: calib.txt."""
import io, json, re, subprocess, sys, urllib.request
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pypdf"], check=False)
UA = "Mozilla/5.0 (compatible; solar-alerts-rss-probe/1.0; +https://github.com/SBDSBKitchener/SolarAlert)"
URLS = ["https://agupubs.onlinelibrary.wiley.com/doi/pdfdirect/10.1029/2019SW002278",
        "https://agupubs.onlinelibrary.wiley.com/doi/pdf/10.1029/2019SW002278",
        "https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2019SW002278",
        "https://doi.org/10.1029/2019SW002278",
        "https://europepmc.org/search?query=DOI%3A10.1029%2F2019SW002278",
        "https://www.ncbi.nlm.nih.gov/pmc/?term=10.1029%2F2019SW002278"]
log, body = [], None
for u in URLS:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "application/pdf,text/html"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            log.append(f"{u}: {r.status} {r.headers.get('Content-Type')} {len(data)} bytes")
            if data[:4] == b"%PDF":
                body = data
                break
    except Exception as e:
        log.append(f"{u}: {str(e)[:120]}")
text = "\n".join(log) + "\n"
if body:
    from pypdf import PdfReader
    pages = [p.extract_text() or "" for p in PdfReader(io.BytesIO(body)).pages]
    full = "\n".join(pages)
    text += f"\nPDF OK: {len(pages)} pages, {len(full)} chars\n\n"
    open("calib_paper.txt", "w").write(full)
    # lines mentioning solar wind quantities or dB/dt
    for m in re.finditer(r"[^.\n]*(km\s?/\s?s|nT\s?/\s?min|cm\W{0,3}3|IMP|shock)[^.\n]*\.", full):
        text += m.group(0).strip() + "\n"
open("calib.txt", "w").write(text)
print(text[:3000])
