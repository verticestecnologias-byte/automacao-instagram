"""
Robô de publicação da Vértices Tecnologias no Instagram.

Roda de 30 em 30 minutos pelo GitHub Actions. A cada rodada:
  1. olha a fila/ (pastas AAAA-MM-DD_produto)
  2. se chegou a hora do post daquele dia, publica o post (foto única ou carrossel)
  3. se chegou a hora do story (e o post já saiu), publica o story
  4. quando post e story saíram, move a pasta para postados/
  5. avisa (abrindo uma "issue" no GitHub, que chega por e-mail) quando a fila
     estiver acabando ou quando o token do Instagram parar de funcionar

Variáveis de ambiente (configuradas como "secrets" no GitHub):
  IG_USER_ID       id da conta profissional do Instagram
  IG_ACCESS_TOKEN  token de acesso (de preferência de "usuário do sistema", que não expira)
  DRY_RUN          "true" para só simular, sem publicar nada

Uso:
  python publicar.py               publica o que estiver na hora
  python publicar.py --verificar   só confere token e tamanho da fila
  python publicar.py --agenda      mostra a agenda da fila
"""
import json
import os
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

RAIZ = Path(__file__).resolve().parent
FILA = RAIZ / "fila"
POSTADOS = RAIZ / "postados"
CONFIG = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
ESTADO_ARQ = RAIZ / "estado.json"

FUSO = ZoneInfo(CONFIG.get("fuso", "America/Sao_Paulo"))
API = os.environ.get("IG_API_BASE", CONFIG.get("api", "https://graph.facebook.com/v23.0")).rstrip("/")
IG_USER_ID = os.environ.get("IG_USER_ID", "")
TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
REPO = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")

DIAS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]


# ---------------------------------------------------------------- utilidades

def agora():
    return datetime.now(FUSO)


def ler_estado():
    if ESTADO_ARQ.exists():
        return json.loads(ESTADO_ARQ.read_text(encoding="utf-8"))
    return {}


def salvar_estado(estado):
    ESTADO_ARQ.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


def data_da_pasta(pasta: Path):
    try:
        return date.fromisoformat(pasta.name[:10])
    except ValueError:
        return None


def horarios(dia: date):
    padrao = CONFIG["horarios"].get("padrao", {"post": "19:00", "story": "21:00"})
    h = CONFIG["horarios"].get(DIAS[dia.weekday()], padrao)

    def junta(hhmm):
        hh, mm = map(int, hhmm.split(":"))
        return datetime(dia.year, dia.month, dia.day, hh, mm, tzinfo=FUSO)

    return junta(h["post"]), junta(h["story"])


def url_publica(arquivo: Path):
    caminho = arquivo.relative_to(RAIZ).as_posix()
    return f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{quote(caminho)}"


def fotos(pasta: Path):
    return sorted(pasta.glob("[0-9][0-9].jpg"))


def log(msg):
    print(f"[{agora():%d/%m %H:%M}] {msg}", flush=True)


# ---------------------------------------------------------------- API do Instagram

class ErroInstagram(Exception):
    pass


def chamar(metodo, caminho, **params):
    params["access_token"] = TOKEN
    url = f"{API}/{caminho}"
    for tentativa in range(3):
        r = requests.request(metodo, url, data=params if metodo == "POST" else None,
                             params=params if metodo == "GET" else None, timeout=60)
        if r.status_code < 500:
            break
        time.sleep(10 * (tentativa + 1))
    dados = r.json() if r.content else {}
    if r.status_code >= 400 or "error" in dados:
        erro = dados.get("error", {})
        raise ErroInstagram(f"{erro.get('message', r.text)} (código {erro.get('code')})")
    return dados


def esperar_container(container_id, limite_s=300):
    inicio = time.time()
    while time.time() - inicio < limite_s:
        status = chamar("GET", container_id, fields="status_code,status").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise ErroInstagram(f"o Instagram recusou a mídia (status {status})")
        time.sleep(5)
    raise ErroInstagram("o Instagram demorou demais para processar a mídia")


def publicar_container(container_id):
    esperar_container(container_id)
    return chamar("POST", f"{IG_USER_ID}/media_publish", creation_id=container_id)["id"]


def publicar_post(pasta: Path):
    imagens = fotos(pasta)
    legenda = (pasta / "caption.txt").read_text(encoding="utf-8").strip()
    if not imagens:
        raise ErroInstagram(f"{pasta.name}: nenhuma foto 01.jpg encontrada")
    if len(legenda) > 2200:
        raise ErroInstagram(f"{pasta.name}: legenda passa de 2200 caracteres")
    if legenda.count("#") > 30:
        raise ErroInstagram(f"{pasta.name}: mais de 30 hashtags")

    if DRY_RUN:
        log(f"(simulação) post de {pasta.name} com {len(imagens)} foto(s): {[url_publica(i) for i in imagens]}")
        return "simulado"

    if len(imagens) == 1:
        c = chamar("POST", f"{IG_USER_ID}/media", image_url=url_publica(imagens[0]), caption=legenda)["id"]
    else:
        filhos = []
        for img in imagens[:10]:
            filho = chamar("POST", f"{IG_USER_ID}/media", image_url=url_publica(img), is_carousel_item="true")["id"]
            esperar_container(filho)
            filhos.append(filho)
        c = chamar("POST", f"{IG_USER_ID}/media", media_type="CAROUSEL",
                   children=",".join(filhos), caption=legenda)["id"]
    return publicar_container(c)


def publicar_story(pasta: Path):
    story = pasta / "story.jpg"
    if DRY_RUN:
        log(f"(simulação) story de {pasta.name}: {url_publica(story)}")
        return "simulado"
    c = chamar("POST", f"{IG_USER_ID}/media", image_url=url_publica(story), media_type="STORIES")["id"]
    return publicar_container(c)


# ---------------------------------------------------------------- avisos

def abrir_aviso(titulo, corpo, estado, chave, intervalo_dias=7):
    """Abre uma issue no GitHub (o GitHub manda por e-mail). No máximo 1 por semana por assunto."""
    ultimo = estado.get("avisos", {}).get(chave)
    hoje = agora().date()
    if ultimo and hoje - date.fromisoformat(ultimo) < timedelta(days=intervalo_dias):
        return
    log(f"AVISO: {titulo}")
    if REPO and GH_TOKEN and not DRY_RUN:
        requests.post(f"https://api.github.com/repos/{REPO}/issues",
                      headers={"Authorization": f"Bearer {GH_TOKEN}",
                               "Accept": "application/vnd.github+json"},
                      json={"title": titulo, "body": corpo}, timeout=30)
    estado.setdefault("avisos", {})[chave] = hoje.isoformat()


def checar_fila(estado):
    pendentes = [p for p in FILA.iterdir() if p.is_dir() and data_da_pasta(p)]
    datas = sorted(data_da_pasta(p) for p in pendentes)
    hoje = agora().date()
    dias_restantes = (datas[-1] - hoje).days if datas else -1
    minimo = CONFIG.get("avisar_quando_faltar_dias", 10)
    if dias_restantes < minimo:
        fim = datas[-1].strftime("%d/%m/%Y") if datas else "já acabou"
        abrir_aviso(
            "📭 Fila do Instagram acabando",
            f"A fila de postagens termina em **{fim}** ({max(dias_restantes, 0)} dias).\n\n"
            "Coloque novas pastas em `fila/` (formato `AAAA-MM-DD_produto`, com as fotos, "
            "`caption.txt` e `story_caption.txt`) para o robô continuar postando.",
            estado, "fila")
    return dias_restantes


def checar_token(estado):
    if DRY_RUN:
        return True
    if not TOKEN or not IG_USER_ID:
        log("ERRO: faltam os segredos IG_USER_ID e/ou IG_ACCESS_TOKEN no GitHub.")
        return False
    try:
        conta = chamar("GET", IG_USER_ID, fields="username")
        log(f"token OK — conta @{conta.get('username')}")
        estado.get("avisos", {}).pop("token", None)
        return True
    except Exception as e:  # noqa: BLE001
        abrir_aviso(
            "🔑 Token do Instagram parou de funcionar",
            f"O robô não conseguiu acessar a conta: `{e}`\n\n"
            "Gere um novo token (veja o passo 3 do LEIAME.md) e atualize o segredo "
            "`IG_ACCESS_TOKEN` em Settings → Secrets and variables → Actions.",
            estado, "token", intervalo_dias=1)
        return False


# ---------------------------------------------------------------- rodada principal

def mover_para_postados(pasta: Path):
    POSTADOS.mkdir(exist_ok=True)
    destino = POSTADOS / pasta.name
    if destino.exists():
        shutil.rmtree(destino)
    shutil.move(str(pasta), str(destino))
    log(f"{pasta.name} concluído → postados/")


def rodada():
    estado = ler_estado()
    pub = estado.setdefault("publicados", {})
    t = agora()
    ultimo_post = estado.get("ultimo_post")
    espaco = timedelta(hours=CONFIG.get("intervalo_minimo_horas", 3))
    falhou = False

    if not DRY_RUN and (not IG_USER_ID or not TOKEN):
        log("ERRO: faltam os segredos IG_USER_ID e IG_ACCESS_TOKEN no GitHub.")
        sys.exit(1)

    for pasta in sorted(p for p in FILA.iterdir() if p.is_dir()):
        dia = data_da_pasta(pasta)
        if not dia or dia > t.date():
            continue
        hora_post, hora_story = horarios(dia)
        reg = pub.setdefault(pasta.name, {})

        try:
            if "post" not in reg and t >= hora_post:
                # nunca dois posts colados: se ficou algo atrasado, sai um por vez
                if ultimo_post and t - datetime.fromisoformat(ultimo_post) < espaco:
                    log(f"{pasta.name}: post atrasado, aguardando intervalo mínimo")
                    continue
                reg["post"] = publicar_post(pasta)
                reg["post_em"] = t.isoformat(timespec="minutes")
                estado["ultimo_post"] = t.isoformat()
                ultimo_post = estado["ultimo_post"]
                log(f"✅ post publicado: {pasta.name}")

            tem_story = (pasta / "story.jpg").exists()
            espera_story = timedelta(minutes=CONFIG.get("story_minutos_depois_do_post", 60))
            story_liberado = "post_em" in reg and t >= datetime.fromisoformat(reg["post_em"]) + espera_story
            if "post" in reg and tem_story and "story" not in reg and t >= hora_story and story_liberado:
                reg["story"] = publicar_story(pasta)
                reg["story_em"] = t.isoformat(timespec="minutes")
                log(f"✅ story publicado: {pasta.name}")

            if "post" in reg and ("story" in reg or not tem_story) and not DRY_RUN:
                mover_para_postados(pasta)
        except ErroInstagram as e:
            falhou = True
            log(f"❌ {pasta.name}: {e}")
            reg["ultimo_erro"] = f"{t:%d/%m %H:%M} {e}"
        finally:
            if not DRY_RUN:
                salvar_estado(estado)

    checar_fila(estado)
    if not DRY_RUN:
        salvar_estado(estado)
    if falhou:
        checar_token(estado)
        salvar_estado(estado)
        sys.exit(1)  # faz o GitHub marcar a rodada como falha e mandar e-mail


def agenda():
    for pasta in sorted(p for p in FILA.iterdir() if p.is_dir()):
        dia = data_da_pasta(pasta)
        if not dia:
            print(f"(sem data) {pasta.name}")
            continue
        hp, hs = horarios(dia)
        print(f"{DIAS[dia.weekday()]} {dia:%d/%m/%Y}  post {hp:%H:%M}  story {hs:%H:%M}  "
              f"{len(fotos(pasta))} foto(s)  {pasta.name[11:]}")


def proxima():
    """Segundos até o próximo post/story pendente (0 se já está na hora)."""
    pub = ler_estado().get("publicados", {})
    t = agora()
    espera_story = timedelta(minutes=CONFIG.get("story_minutos_depois_do_post", 60))
    eventos = []
    for pasta in sorted(p for p in FILA.iterdir() if p.is_dir()):
        dia = data_da_pasta(pasta)
        if not dia:
            continue
        hp, hs = horarios(dia)
        reg = pub.get(pasta.name, {})
        if "post" not in reg:
            eventos.append(hp)
        elif "story" not in reg and (pasta / "story.jpg").exists():
            eventos.append(max(hs, datetime.fromisoformat(reg["post_em"]) + espera_story))
    if not eventos:
        return 6 * 3600
    return max(0, int((min(eventos) - t).total_seconds()))


if __name__ == "__main__":
    if "--proxima" in sys.argv:
        print(proxima())
    elif "--agenda" in sys.argv:
        agenda()
    elif "--verificar" in sys.argv:
        est = ler_estado()
        ok = checar_token(est)
        dias = checar_fila(est)
        log(f"fila com {dias} dia(s) de conteúdo")
        salvar_estado(est)
        sys.exit(0 if ok else 1)
    else:
        rodada()
