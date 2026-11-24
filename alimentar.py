"""
Alimentador da fila: traz os lançamentos da STLflix para a fila do Instagram.

Toda semana (pelo GitHub Actions):
  1. pega a lista pública dos mais baixados da STLflix (10 e 30 dias)
  2. fica só com lançamentos recentes que ainda não foram usados
  3. baixa as fotos da galeria de cada um (sem telas do fatiador nem fotos em fundo claro)
  4. escreve a legenda no padrão da Vértices (com Claude, se houver ANTHROPIC_API_KEY;
     senão, com um modelo pronto)
  5. agenda nos próximos dias livres de postagem e registra o produto como usado

Os produtos entram com antecedência (config: manter_fila_dias), então dá tempo de
vetar: é só apagar a pasta do produto em fila/.

Uso:
  python alimentar.py               adiciona à fila
  python alimentar.py --simular     só mostra o que faria
"""
import html
import io
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image

RAIZ = Path(__file__).resolve().parent
FILA = RAIZ / "fila"
POSTADOS = RAIZ / "postados"
USADOS_ARQ = RAIZ / "stlflix_usados.json"
CONFIG = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
CFG = CONFIG.get("alimentador", {})
FUSO = ZoneInfo(CONFIG.get("fuso", "America/Sao_Paulo"))

SITE = "https://platform.stlflix.com"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
DIAS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]
FOTO_RUIM = re.compile(r"mono ?colou?r|multi ?colou?r|multi ?part|plate|thumb|print[_ ]?bed|_EN_",
                       re.IGNORECASE)
SIMULAR = "--simular" in sys.argv
FORCAR = "--forcar" in sys.argv  # ignora "fila cheia" (para testar)
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def log(msg):
    print(msg, flush=True)


def slugify(txt):
    txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", txt.lower()).strip("-")[:60]


# ---------------------------------------------------------------- STLflix

def mais_baixados():
    vistos = {}
    for intervalo in ("LAST_10_DAYS", "LAST_30_DAYS"):
        r = requests.get(f"{SITE}/api/top-downloads", params={"interval": intervalo},
                         headers=UA, timeout=40)
        r.raise_for_status()
        for item in r.json():
            prod = ((item.get("product") or {}).get("data") or {})
            a = prod.get("attributes") or {}
            slug = a.get("slug")
            if not slug:
                continue
            downloads = int(item.get("total_downloads") or 0)
            if slug not in vistos or downloads > vistos[slug]["downloads"]:
                vistos[slug] = {"slug": slug, "nome_en": a.get("name", slug),
                                "lancamento": (a.get("release_date") or "")[:10],
                                "downloads": downloads}
    return sorted(vistos.values(), key=lambda x: -x["downloads"])


def dados_produto(slug):
    r = requests.get(f"{SITE}/pt-BR/product/{slug}", headers=UA, timeout=40)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if not m:
        raise RuntimeError("página do produto sem dados")
    pp = json.loads(m.group(1))["props"]["pageProps"]
    galeria = [g["attributes"]["url"] for g in ((pp.get("gallery") or {}).get("data") or [])
               if g.get("attributes", {}).get("url")]
    desc = html.unescape(re.sub(r"<[^>]+>", " ", pp.get("description") or ""))
    desc = re.sub(r"\s+", " ", desc).strip()
    drop = (((pp.get("drop") or {}).get("data") or {}).get("attributes") or {}).get("title", "")
    return {"nome": pp.get("name") or slug, "descricao": desc, "galeria": galeria,
            "drop": drop, "url": f"{SITE}/pt-BR/product/{slug}"}


def foto_boa(img):
    """Foto de produto da STLflix: fundo preto. Telas do fatiador e fotos na mochila têm fundo claro."""
    pequena = img.convert("L").resize((60, 60))
    px = pequena.load()
    borda = [px[x, y] for x in range(60) for y in range(60) if x < 4 or y < 4 or x > 55 or y > 55]
    return sum(borda) / len(borda) < 45


def baixar_fotos(urls):
    boas = []
    for url in urls:
        nome = url.rsplit("/", 1)[-1]
        if not nome.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) or FOTO_RUIM.search(nome):
            continue
        try:
            r = requests.get(url, headers=UA, timeout=60)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content))
            img.load()
        except Exception as e:  # noqa: BLE001
            log(f"    foto ignorada ({nome}): {e}")
            continue
        if foto_boa(img):
            boas.append((nome, img))
        if len(boas) >= CFG.get("max_fotos", 7):
            break
    return boas


# ---------------------------------------------------------------- legenda

EXEMPLO = (RAIZ / "exemplo_legenda.txt").read_text(encoding="utf-8") if (RAIZ / "exemplo_legenda.txt").exists() else ""

REGRAS = """Você escreve posts de Instagram para a Vértices Tecnologias (@vertices_tecnologias),
empresa de impressão 3D de Passo Fundo/RS. Escreva em português do Brasil, no padrão abaixo:
1. Gancho de uma linha, chamativo e com humor, terminando com 1 ou 2 emojis.
2. "Apresentamos o/a <Nome>, ..." descrevendo a peça com personalidade.
3. Um parágrafo de detalhes (o que chama atenção, como funciona).
4. "Perfeito/a pra ..." com usos e para quem presentear, terminando com 🏆 em algum parágrafo.
5. "🎨 Disponível em várias cores e tamanhos, feito especialmente pra você!"
6. "📩 Manda uma DM agora e garanta o seu!" (ajuste o gênero)
7. "@vertices_tecnologias"
8. 8 a 11 hashtags, sempre incluindo #Impressao3D #VerticesTecnologias.
Regras: NUNCA cite preço. NÃO invente características que a descrição não menciona
(só diga "articulado" se a descrição disser). Não mencione STLflix. Dê à peça um nome
criativo em português (pode adaptar o nome original).
Responda SOMENTE com JSON: {"nome": "...", "legenda": "...", "frase_story": "..."}
onde frase_story tem no máximo 60 caracteres, sem emoji, e chama atenção para a peça."""


def legenda_claude(prod):
    corpo = {
        "model": CFG.get("modelo", "claude-haiku-4-5-20251001"),
        "max_tokens": 1500,
        "system": REGRAS + ("\n\nExemplo de legenda aprovada:\n" + EXEMPLO if EXEMPLO else ""),
        "messages": [{"role": "user", "content":
                      f"Produto: {prod['nome']}\nDescrição do fabricante: {prod['descricao'][:1500]}"}],
    }
    r = requests.post("https://api.anthropic.com/v1/messages", json=corpo, timeout=90,
                      headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"})
    r.raise_for_status()
    texto = "".join(b.get("text", "") for b in r.json().get("content", []))
    dados = json.loads(re.search(r"\{.*\}", texto, re.S).group(0))
    return dados["nome"].strip(), dados["legenda"].strip(), dados["frase_story"].strip()


def legenda_modelo(prod):
    nome = prod["nome"]
    frase = re.split(r"(?<=[.!?])\s", prod["descricao"])[0][:280] if prod["descricao"] else ""
    legenda = (f"Novidade fresquinha saindo da impressora! ✨🖨️\n\n"
               f"Apresentamos {nome}. {frase}\n\n"
               f"Cada detalhe é impresso em 3D aqui na Vértices, com acabamento caprichado. 🏆\n\n"
               f"Perfeito pra decorar, colecionar ou presentear alguém especial.\n\n"
               f"🎨 Disponível em várias cores e tamanhos, feito especialmente pra você!\n\n"
               f"📩 Manda uma DM agora e garanta o seu!\n\n@vertices_tecnologias\n\n"
               f"#{slugify(nome).replace('-', '').title()} #PeçaÚnica #PresenteCriativo "
               f"#Decoração #Colecionavel #Impressao3D #VerticesTecnologias")
    return nome, legenda, "Novidade na Vértices! Já viu essa?"


def escrever(prod):
    if ANTHROPIC_KEY:
        try:
            return legenda_claude(prod)
        except Exception as e:  # noqa: BLE001
            log(f"    Claude falhou ({e}); usando modelo pronto")
    return legenda_modelo(prod)


# ---------------------------------------------------------------- agenda

def datas_ocupadas():
    ocupadas = set()
    for base in (FILA, POSTADOS):
        if base.exists():
            for p in base.iterdir():
                try:
                    ocupadas.add(date.fromisoformat(p.name[:10]))
                except ValueError:
                    pass
    return ocupadas


def proximas_datas(qtd):
    dias_ok = {DIAS.index(d) for d in CFG.get("dias_postagem", ["ter", "qua", "qui", "dom"])}
    ocupadas = datas_ocupadas()
    d = datetime.now(FUSO).date() + timedelta(days=CFG.get("antecedencia_minima_dias", 3))
    saida = []
    while len(saida) < qtd:
        if d.weekday() in dias_ok and d not in ocupadas:
            saida.append(d)
        d += timedelta(days=1)
    return saida


def dias_de_fila():
    hoje = datetime.now(FUSO).date()
    futuras = [d for d in datas_ocupadas() if d >= hoje]
    return (max(futuras) - hoje).days if futuras else 0


# ---------------------------------------------------------------- principal

def main():
    usados = json.loads(USADOS_ARQ.read_text(encoding="utf-8")) if USADOS_ARQ.exists() else []
    alvo = CFG.get("manter_fila_dias", 21)
    falta = dias_de_fila()
    log(f"fila cobre {falta} dias; alvo {alvo} dias")
    if falta >= alvo and not FORCAR:
        log("fila cheia, nada a fazer")
        return []

    hoje = datetime.now(FUSO).date()
    desde = max(date.fromisoformat(CFG.get("lancamento_desde", "2026-10-20")),
                hoje - timedelta(days=CFG.get("lancamento_max_dias", 60)))
    candidatos = [c for c in mais_baixados()
                  if c["slug"] not in usados and c["lancamento"] and date.fromisoformat(c["lancamento"]) >= desde]
    log(f"{len(candidatos)} lançamento(s) novo(s) desde {desde:%d/%m/%Y}")

    por_semana = len(CFG.get("dias_postagem", ["ter", "qua", "qui", "dom"]))
    quantos = min(CFG.get("max_por_rodada", 6), max(1, round((alvo - falta) / 7 * por_semana)))
    if FORCAR:
        quantos = CFG.get("max_por_rodada", 6)
    datas = proximas_datas(quantos)
    adicionados = []

    for cand in candidatos:
        if len(adicionados) >= quantos:
            break
        log(f"- {cand['nome_en']} ({cand['downloads']} downloads, lançado {cand['lancamento']})")
        try:
            prod = dados_produto(cand["slug"])
            fotos = baixar_fotos(prod["galeria"])
        except Exception as e:  # noqa: BLE001
            log(f"    pulado: {e}")
            continue
        if len(fotos) < CFG.get("min_fotos", 2):
            log(f"    pulado: só {len(fotos)} foto(s) boa(s)")
            usados.append(cand["slug"])
            continue

        nome, legenda, frase = escrever(prod)
        if len(adicionados) >= len(datas):
            break
        dia = datas[len(adicionados)]
        if dia <= date(dia.year, 12, 18) and dia.month == 12 and "Natal" not in legenda:
            legenda = legenda.replace("📩", "🎄 Encomende já o seu presente de Natal!\n\n📩", 1)
        pasta = FILA / f"{dia.isoformat()}_{slugify(nome) or cand['slug']}"
        log(f"    → {dia:%d/%m} {pasta.name} ({len(fotos)} fotos)")
        if not SIMULAR:
            pasta.mkdir(parents=True, exist_ok=True)
            for i, (arq, img) in enumerate(fotos, start=1):
                img.convert("RGB").save(pasta / f"f{i:02d}_{Path(arq).stem[:40]}.jpg", "JPEG", quality=92)
            (pasta / "caption.txt").write_text(legenda + "\n", encoding="utf-8")
            frase = frase.rstrip(".!? ") + "!"
            (pasta / "story_caption.txt").write_text(frase + " Post completo no feed!\n", encoding="utf-8")
            (pasta / "origem.txt").write_text(f"{cand['slug']}\n{prod['url']}\n{prod['drop']}\n", encoding="utf-8")
            usados.append(cand["slug"])
        adicionados.append({"data": dia.strftime("%d/%m"), "nome": nome, "fotos": len(fotos),
                            "pasta": pasta.name, "url": prod["url"]})

    if not SIMULAR:
        USADOS_ARQ.write_text(json.dumps(sorted(set(usados)), indent=1), encoding="utf-8")
        Path(RAIZ / "ultima_alimentacao.json").write_text(
            json.dumps(adicionados, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{len(adicionados)} post(s) adicionado(s)")
    if not adicionados and falta < 10:
        log("AVISO: nenhum lançamento novo encontrado e a fila está curta")
    return adicionados


def avisar(adicionados):
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if SIMULAR or not adicionados or not (repo and token):
        return
    linhas = "\n".join(f"- **{a['data']}** · {a['nome']} ({a['fotos']} fotos) · "
                       f"[pasta](https://github.com/{repo}/tree/main/fila/{a['pasta']}) · [STLflix]({a['url']})"
                       for a in adicionados)
    corpo = (f"O alimentador colocou **{len(adicionados)} lançamento(s)** na fila:\n\n{linhas}\n\n"
             "Se não quiser algum, apague a pasta dele em `fila/` antes da data. "
             "Se quiser mudar a legenda, edite o `caption.txt` da pasta.")
    requests.post(f"https://api.github.com/repos/{repo}/issues",
                  headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                  json={"title": f"🆕 {len(adicionados)} post(s) novo(s) na fila do Instagram", "body": corpo},
                  timeout=30)


if __name__ == "__main__":
    avisar(main())
