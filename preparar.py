"""
Prepara as pastas da fila para publicação.

Para cada pasta em fila/ (nome: AAAA-MM-DD_produto):
  - converte as fotos do feed para 01.jpg, 02.jpg ... (JPEG 1080x1350, fundo preto)
  - gera story.jpg (1080x1920) a partir da 1ª foto + story_caption.txt, se ainda não existir

Pode rodar quantas vezes quiser: o que já está pronto não é refeito.

Uso:
    python preparar.py            # prepara todas as pastas da fila
    python preparar.py fila/2027-01-20_produto   # só uma pasta
"""
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

RAIZ = Path(__file__).resolve().parent
FILA = RAIZ / "fila"
FONTE = RAIZ / "fontes" / "DejaVuSans-Bold.ttf"
ARROBA = "@vertices_tecnologias"

FEED = (1080, 1350)
STORY = (1080, 1920)
MAX_FOTOS = 10  # limite do carrossel do Instagram
EXT_IMG = {".jpg", ".jpeg", ".png", ".webp"}
NOME_PRONTO = re.compile(r"^\d{2}\.jpg$")


def encaixar(img, tamanho, fundo=(0, 0, 0)):
    """Encaixa a imagem inteira no tamanho pedido, sem cortar, completando com fundo preto."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail(tamanho, Image.LANCZOS)
    tela = Image.new("RGB", tamanho, fundo)
    tela.paste(img, ((tamanho[0] - img.width) // 2, (tamanho[1] - img.height) // 2))
    return tela


def quebrar(draw, texto, fonte, largura_max):
    linhas, atual = [], ""
    for palavra in texto.split():
        teste = (atual + " " + palavra).strip()
        if draw.textlength(teste, font=fonte) <= largura_max:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


def sem_emoji(texto):
    # a fonte não desenha emoji; o texto do story fica só com letras
    return re.sub(r"[^\w\s.,!?;:'\"()\-–—/%&+#@ÀÁÂÃÇÉÊÍÓÔÕÚÜàáâãçéêíóôõúü]", "", texto).strip()


def gerar_story(foto, legenda, destino):
    """Story no modelo da Vértices: arroba no topo, foto no meio, frase em branco embaixo."""
    tela = Image.new("RGB", STORY, (0, 0, 0))
    d = ImageDraw.Draw(tela)

    f_topo = ImageFont.truetype(str(FONTE), 44)
    w = d.textlength(ARROBA, font=f_topo)
    d.text(((STORY[0] - w) / 2, 92), ARROBA, font=f_topo, fill="white")

    img = ImageOps.exif_transpose(Image.open(foto)).convert("RGB")
    img.thumbnail((STORY[0], 1260), Image.LANCZOS)
    y_foto = 230 + (1260 - img.height) // 2
    tela.paste(img, ((STORY[0] - img.width) // 2, y_foto))

    texto = sem_emoji(legenda)
    if texto:
        tamanho = 76
        while tamanho > 44:
            f = ImageFont.truetype(str(FONTE), tamanho)
            linhas = quebrar(d, texto, f, 920)
            if len(linhas) <= 3:
                break
            tamanho -= 4
        alt_linha = int(tamanho * 1.18)
        y = 1880 - alt_linha * len(linhas) - 40
        for linha in linhas:
            lw = d.textlength(linha, font=f)
            d.text(((STORY[0] - lw) / 2, y), linha, font=f, fill="white")
            y += alt_linha

    tela.save(destino, "JPEG", quality=90)


def valida(p: Path):
    """True se a imagem abre. Se estiver corrompida, separa o arquivo e avisa."""
    try:
        with Image.open(p) as im:
            im.verify()
        return True
    except Exception:  # noqa: BLE001
        p.rename(p.with_name(p.name + ".corrompida"))
        print(f"  AVISO: {p.name} está corrompida e foi deixada de lado")
        return False


def preparar_pasta(pasta: Path):
    for p in list(pasta.iterdir()):
        if p.suffix.lower() in EXT_IMG:
            valida(p)

    fotos_prontas = sorted(p for p in pasta.iterdir() if NOME_PRONTO.match(p.name))
    soltas = sorted(
        p for p in pasta.iterdir()
        if p.suffix.lower() in EXT_IMG
        and not NOME_PRONTO.match(p.name)
        and not p.stem.lower().startswith("story")
    )

    if soltas:
        n = len(fotos_prontas)
        for p in soltas:
            if n >= MAX_FOTOS:
                print(f"  aviso: {p.name} ignorada (máximo de {MAX_FOTOS} fotos)")
                continue
            n += 1
            encaixar(Image.open(p), FEED).save(pasta / f"{n:02d}.jpg", "JPEG", quality=90)
            p.unlink()
        print(f"  {len(soltas)} foto(s) convertida(s)")

    for p in sorted(pasta.glob("[0-9][0-9].jpg")):
        with Image.open(p) as im:
            precisa = im.size != FEED or im.format != "JPEG"
        if precisa:
            encaixar(Image.open(p), FEED).save(p, "JPEG", quality=90)

    # renumera 01, 02, 03... sem buracos (caso alguma foto tenha sido descartada)
    for i, p in enumerate(sorted(pasta.glob("[0-9][0-9].jpg")), start=1):
        if p.name != f"{i:02d}.jpg":
            p.rename(pasta / f"{i:02d}.jpg")

    story = pasta / "story.jpg"
    base = next((p for p in pasta.iterdir() if p.stem.lower() == "story_base"), None) or pasta / "01.jpg"
    legenda = (pasta / "story_caption.txt")
    if not story.exists() and base.exists():
        gerar_story(base, legenda.read_text(encoding="utf-8") if legenda.exists() else "", story)
        print("  story.jpg gerado")
    if base.name.startswith("story_base") and story.exists():
        base.unlink()

    if not (pasta / "caption.txt").exists():
        print("  ATENÇÃO: falta caption.txt")


def main():
    alvos = [Path(a) for a in sys.argv[1:]] or sorted(p for p in FILA.iterdir() if p.is_dir())
    for pasta in alvos:
        print(pasta.name)
        preparar_pasta(pasta)


if __name__ == "__main__":
    main()
