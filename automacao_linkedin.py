"""
Automação de postagem das anotações no LinkedIn.

Fluxo: valida o access token -> obtém a URN -> seleciona a próxima
anotação .md -> reescreve com o Gemini -> descreve uma cena visual do
post -> gera uma imagem (IA, com fallback) -> publica no LinkedIn
(Posts API) com a imagem anexada.

O LinkedIn não emite refresh token para apps de perfil pessoal, então o
access token (validade ~60 dias) é usado diretamente. Quando ele expira,
o script cria a flag `.token_expired` e o workflow abre uma GitHub Issue
de lembrete para renovar.

Variáveis de ambiente (GitHub Secrets):
  GEMINI_API_KEY
  LINKEDIN_ACCESS_TOKEN
Opcionais (com defaults):
  CONTENT_DIR          (default: content)  pasta raiz varrida em busca dos .md
  GEMINI_TEXT_MODEL    (default: gemini-2.5-flash)
  GEMINI_IMAGE_MODEL   (default: imagen-3.0-generate-002)
  LINKEDIN_VERSION     (default: 202606)
  DRY_RUN              ("1" = monta tudo mas NÃO publica)
  GEMINI_RETRY_MAX     (default: 5)  tentativas no texto p/ erro transitório
  GEMINI_RETRY_MAX_IMG (default: 3)  tentativas na imagem (tem fallback de card)
  GEMINI_RETRY_BASE    (default: 2.0) base do backoff exponencial, em segundos
  USE_POLLINATIONS     (default: "1") usa Pollinations.ai (imagem IA grátis)
  POLLINATIONS_MODEL   (default: flux) modelo do Pollinations (ex.: flux, turbo)
  IMG_TEXT_CHECK       (default: "1") verifica se a imagem saiu com texto
  IMG_RETRY_SEEDS      (default: 3)  tentativas (seeds) até sair sem texto
"""

import os
import re
import sys
import time
import hashlib
import textwrap
import requests
from google import genai

# ───────────────────────── Configuração ─────────────────────────
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
ACCESS_TOKEN = os.getenv("LINKEDIN_ACCESS_TOKEN")

TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "imagen-4.0-fast-generate-001")
IMAGE_MODEL_ALT = os.getenv("GEMINI_IMAGE_MODEL_ALT", "gemini-2.5-flash-image")
LI_VERSION = os.getenv("LINKEDIN_VERSION", "202606")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

# Pollinations.ai: geração de imagem por IA gratuita (sem API key) — fonte
# primária, já que o Gemini não tem mais free tier de imagem (jun/2026).
USE_POLLINATIONS = os.getenv("USE_POLLINATIONS", "1") == "1"
POLLINATIONS_MODEL = os.getenv("POLLINATIONS_MODEL", "flux")

# O Pollinations não aceita negative_prompt, então a ausência de texto na arte
# depende do prompt + de conferir a imagem gerada e refazer com outro seed.
IMG_TEXT_CHECK = os.getenv("IMG_TEXT_CHECK", "1") == "1"
IMG_RETRY_SEEDS = int(os.getenv("IMG_RETRY_SEEDS", "3"))

# pasta raiz varrida em busca das anotações .md (desacopla o motor do conteúdo)
CONTENT_DIR = os.getenv("CONTENT_DIR", "content")
INDEX_FILE = "post_index.txt"
TOKEN_FLAG = ".token_expired"        # sinaliza ao workflow que o token caducou
IMG_OUT = "post_image.png"           # imagem gerada (efêmera)
FALLBACK_IMG = "assets/post_fallback.png"  # banner de marca (último recurso)
FONT_BOLD = "assets/fonts/DejaVuSans-Bold.ttf"
FONT_REG = "assets/fonts/DejaVuSans.ttf"
# tons secundários do gradiente — variam por post (mantendo a marca azul)
PALETA_CARD = [(25, 60, 120), (40, 30, 110), (15, 70, 95), (60, 25, 90), (20, 52, 72)]

client = genai.Client(api_key=GEMINI_KEY)


def sinalizar_token_expirado(detalhe=""):
    msg = (
        "O access token do LinkedIn expirou ou é inválido.\n\n"
        "Gere um novo em https://www.linkedin.com/developers/tools/oauth/token-generator "
        "(app 'Automação', escopos: openid, profile, w_member_social) e atualize o "
        "secret LINKEDIN_ACCESS_TOKEN do repositório.\n\n"
        f"Detalhe técnico: {detalhe}"
    )
    try:
        with open(TOKEN_FLAG, "w") as f:
            f.write(msg)
    except OSError:
        pass
    print("❌ " + msg)


# ───────────────────────── LinkedIn: identidade ─────────────────────────
def get_my_urn():
    try:
        res = requests.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
            timeout=15,
        )
        if res.status_code == 401:
            sinalizar_token_expirado(f"userinfo 401: {res.text[:200]}")
            return None
        res.raise_for_status()
        sub = res.json().get("sub")
        return f"urn:li:person:{sub}" if sub else None
    except requests.RequestException as e:
        print(f"❌ Erro ao obter URN: {e}")
        return None


# ───────────────────────── Seleção de conteúdo ─────────────────────────
def carregar_proximo_arquivo():
    if not os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "w") as f:
            f.write("0")
    try:
        with open(INDEX_FILE, "r") as f:
            index = int(f.read().strip() or 0)
    except (ValueError, OSError):
        index = 0  # índice corrompido -> recomeça

    arquivos_md = sorted(
        os.path.join(r, f)
        for r, d, fs in os.walk(CONTENT_DIR)
        for f in fs
        if f.endswith(".md") and "README" not in f.upper() and ".github" not in r
    )
    if not arquivos_md:
        return None, 0
    if index >= len(arquivos_md):
        index = 0
    return arquivos_md[index], index


# ───────────────────────── Gemini: retry ─────────────────────────
RETRY_MAX = int(os.getenv("GEMINI_RETRY_MAX", "5"))
RETRY_MAX_IMG = int(os.getenv("GEMINI_RETRY_MAX_IMG", "3"))  # imagem tem fallback (card)
RETRY_BASE = float(os.getenv("GEMINI_RETRY_BASE", "2.0"))  # segundos

# trechos que indicam erro transitório do lado do servidor — vale tentar de novo
_TRANSIENTE = (
    "503", "429", "500", "502", "504",
    "unavailable", "overloaded", "resource_exhausted",
    "deadline", "timeout", "high demand",
)

# trechos que indicam limite PERMANENTE (cota zerada/plano pago) — retentar não
# adianta; melhor desistir já e cair no fallback (card) sem perder ~30s no backoff
_PERMANENTE = (
    "limit: 0", "only available on paid", "paid plan",
    "perday", "per day", "billing", "upgrade your account",
)


def _e_permanente(err):
    """True quando o erro é um limite que não se resolve com nova tentativa
    (cota free-tier zerada, recurso exclusivo de plano pago)."""
    return any(t in str(err).lower() for t in _PERMANENTE)


def _e_quota_por_minuto(err):
    """True para o 429 de requisições por minuto do free tier. O backoff
    exponencial (2s, 4s, 8s) não alcança essa janela: ela só reseta em ~60s."""
    if _e_permanente(err):
        return False
    msg = str(err).lower()
    return "resource_exhausted" in msg or "perminute" in msg


def _e_transitorio(err):
    """True se o erro for transitório (sobrecarga/instabilidade do Gemini),
    sinalizando que uma nova tentativa pode ter sucesso. Limites permanentes
    (cota zerada/plano pago) NÃO contam como transitórios."""
    if _e_permanente(err):
        return False
    code = getattr(err, "code", None)
    if code in (429, 500, 502, 503, 504):
        return True
    msg = str(err).lower()
    return any(t in msg for t in _TRANSIENTE)


def _gemini_com_retry(descricao, fn, tentativas=RETRY_MAX):
    """Executa uma chamada ao Gemini com backoff exponencial (2s, 4s, 8s…)
    para erros transitórios como '503 UNAVAILABLE'. Erros permanentes
    (ex.: API key inválida, cota zerada) sobem na primeira ocorrência."""
    for tentativa in range(1, tentativas + 1):
        try:
            return fn()
        except Exception as e:
            if tentativa >= tentativas or not _e_transitorio(e):
                raise
            espera = RETRY_BASE * (2 ** (tentativa - 1))
            if _e_quota_por_minuto(e):
                espera = max(espera, 62.0)  # aguarda a janela de 1 min reabrir
            print(
                f"⚠️  {descricao}: erro transitório ({e}). "
                f"Tentativa {tentativa}/{tentativas}, aguardando {espera:.0f}s...",
                flush=True,
            )
            time.sleep(espera)


# ───────────────────────── Gemini: texto ─────────────────────────
def gerar_texto(conteudo):
    response = _gemini_com_retry(
        "Gemini (texto)",
        lambda: client.models.generate_content(
        model=TEXT_MODEL,
        contents=(
            "Escreva SEMPRE em português do Brasil (pt-BR), qualquer que seja o idioma do conteúdo base. "
            "Reescreva para eu publicar no LinkedIn, como um hacker avançado em Red Team escreveria. "
            "Tire a mecânica padrão de emojis e as quebras de linha excessivas que entregam que foi gerado por IA. "
            "Converta o conteúdo abaixo em um post para LinkedIn.\n\n"
            f"CONTEÚDO BASE: {conteudo}\n\n"
            "REGRAS CRÍTICAS:\n"
            "1. O POST INTEIRO DEVE ESTAR EM PORTUGUÊS DO BRASIL (pt-BR), incluindo hook, bullets, CTA e hashtags. "
            "Termos técnicos consagrados em inglês (ex.: Red Team, phishing, firewall) podem ser mantidos, mas o texto é em português.\n"
            "2. RESPONDA APENAS COM O TEXTO FINAL DO POST.\n"
            "3. NÃO inclua introduções como 'Aqui está o post' ou 'Claro'.\n"
            "4. NÃO inclua aspas no início ou no fim.\n"
            "5. Use HOOK, bullet points (cada item começando com '• '), tom profissional/direto, CTA e 3 hashtags.\n"
            "6. Máximo 1300 caracteres.\n"
            "7. NÃO USE EMOJIS.\n"
            "8. NÃO use markdown: nada de ** ou * para negrito/itálico, nem # para títulos. "
            "O LinkedIn não renderiza formatação — escreva texto puro."
        ),
        ),
    )
    if not response or not response.text:
        return None
    return _limpar_saida(response.text.strip())


def _limpar_saida(post_final):
    if post_final.startswith("**") and post_final.endswith("**"):
        post_final = post_final[2:-2]
    if post_final.startswith('"') and post_final.endswith('"'):
        post_final = post_final[1:-1]
    if post_final.startswith("```"):
        linhas = post_final.split("\n")
        if len(linhas) > 2:
            post_final = "\n".join(linhas[1:-1])
    intros = ["Aqui está", "Com certeza", "Segue a proposta", "Claro, aqui", "Com certeza!", "Aqui vai"]
    for intro in intros:
        if post_final.lower().startswith(intro.lower()):
            partes = post_final.split("\n", 1)
            if len(partes) > 1:
                post_final = partes[1].strip()
    return _remover_markdown(post_final).strip()


def _remover_markdown(texto):
    """O LinkedIn não renderiza markdown. Converte bullets para '• ' e remove
    negrito/itálico (** * __) que apareceriam literais e denunciam IA."""
    texto = re.sub(r'(?m)^[ \t]*[\*\-\+][ \t]+', '• ', texto)  # bullets -> •
    texto = texto.replace("**", "").replace("__", "").replace("*", "")
    return texto


# ───────────────────────── Gemini: imagem (com fallback) ─────────────────────────
def _titulo_legivel(arquivo):
    """Deriva um título e um subtítulo (disciplina) legíveis a partir do caminho."""
    partes = [p for p in arquivo.replace("./", "").split(os.sep) if p]
    nome = os.path.splitext(partes[-1])[0].strip()
    pai = partes[-2] if len(partes) >= 2 else ""
    avo = partes[-3] if len(partes) >= 3 else ""
    genericos = {"saiba mais", "leia mais", "introducao", "introdução", "conteudo", "conteúdo"}
    titulo = pai if (nome.lower() in genericos and pai) else nome
    sub = (avo or pai or "Segurança da Informação").replace("Disciplina ", "").replace("Disiplina ", "")
    return titulo, sub


def _fonte(tamanho, bold=True):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, tamanho)
    except Exception:
        return ImageFont.load_default()


def gerar_card(titulo, subtitulo):
    """Gera um card visual com o tema do post (varia a cada post = parece autêntico)."""
    try:
        from PIL import Image, ImageDraw
        W, H = 1200, 627
        seed = int(hashlib.md5(titulo.encode("utf-8")).hexdigest(), 16)
        cor2 = PALETA_CARD[seed % len(PALETA_CARD)]
        img = Image.new("RGB", (W, H), "#0d1117")
        d = ImageDraw.Draw(img)
        for y in range(H):  # gradiente vertical (tom secundário varia por post)
            t = y / H
            d.line([(0, y), (W, y)], fill=(
                int(13 + (cor2[0] - 13) * t),
                int(17 + (cor2[1] - 17) * t),
                int(23 + (cor2[2] - 23) * t),
            ))
        d.rectangle([0, 0, W, 8], fill="#1987F0")
        d.rectangle([0, H - 8, W, H], fill="#1987F0")
        d.text((60, 72), subtitulo.upper()[:48], font=_fonte(26, False), fill="#1987F0")
        y = 150
        for linha in textwrap.wrap(titulo, width=24)[:4]:
            d.text((60, y), linha, font=_fonte(54), fill="#ffffff")
            y += 66
        d.text((60, H - 70), "Douglas Cshunderlick  ·  Segurança da Informação",
               font=_fonte(24, False), fill="#c9d1d9")
        img.save(IMG_OUT, "PNG")
        print("🖼️ Card dinâmico gerado (tema do post).")
        return IMG_OUT
    except Exception as e:
        print(f"⚠️ Falha ao gerar card ({e}).")
        return None


# ───────────────────────── Cena visual do post ─────────────────────────
# Cena usada quando o Gemini não descreve uma (falha, cota, resposta vazia).
CENA_PADRAO = (
    "abstract network nodes linked by glowing circuit lines, "
    "faint shield and padlock motifs floating in the dark"
)

# Estilo da marca: só o ASSUNTO da imagem varia por post; a identidade visual não.
ESTILO_MARCA = (
    "Dark navy background, glowing electric blue accents, depth and bokeh, "
    "minimal clean corporate tech aesthetic, cinematic lighting."
)
SEM_TEXTO = (
    "No text, no letters, no words, no numbers, no typography, "
    "no UI, no logos, no watermark."
)

# Substantivos que trazem escrita embutida: pedir "terminal" ou "poster" é pedir
# ao modelo de difusão que escreva alguma coisa. Removidos da cena antes do envio.
_TIPOGRAFICOS = {
    "text", "texts", "letter", "letters", "word", "words", "sign", "signs",
    "poster", "posters", "banner", "banners", "logo", "logos", "screen",
    "screens", "monitor", "monitors", "terminal", "terminals", "code",
    "dashboard", "dashboards", "book", "books", "document", "documents",
    "keyboard", "keyboards", "label", "labels", "title", "titles", "caption",
    "captions", "watermark", "watermarks", "font", "fonts", "billboard",
    "billboards", "newspaper", "newspapers", "typography", "ui",
}


def _higienizar_cena(cena):
    """Rede de segurança sobre a instrução dada ao Gemini: tira da cena tudo que
    convida o modelo de difusão a escrever — números, siglas, nomes próprios,
    aspas e os substantivos que carregam tipografia.

    Devolve "" quando sobra pouco da frase original: uma cena muito mutilada
    ("a showing with a and a on the wall") gera uma imagem pior que a padrão."""
    cena = re.sub(r"[\"'`“”‘’]", " ", cena)
    originais = cena.split()
    palavras = []
    for i, p in enumerate(originais):
        nu = p.strip(".,;:!?()[]-")
        if not nu:
            continue
        if any(c.isdigit() for c in nu):
            continue
        if len(nu) >= 2 and nu.isupper():          # siglas: CVE, RCE, XSS
            continue
        if any(c.isupper() for c in nu[1:]):       # CamelCase: DirtyClone, PowerShell
            continue
        if i > 0 and nu[0].isupper():              # nome próprio no meio da frase
            continue
        if nu.lower() in _TIPOGRAFICOS:
            continue
        palavras.append(p)

    if not originais or len(palavras) < 0.7 * len(originais):
        return ""
    return re.sub(r"\s+", " ", " ".join(palavras)).strip(" .,")


def descrever_cena(texto_post):
    """Pede ao Gemini uma metáfora visual do post, para a imagem conversar com o
    conteúdo. Descreve a metáfora, nunca o tema literal: um prompt com
    'CVE-2026-43503' faz o modelo tentar desenhar essas letras."""
    try:
        resp = _gemini_com_retry(
            "Gemini (cena visual)",
            lambda: client.models.generate_content(
                model=TEXT_MODEL,
                contents=(
                    "Leia o post abaixo e descreva, em inglês, UMA cena visual "
                    "que sirva de metáfora para o tema dele. A cena vira o prompt "
                    "de um gerador de imagens.\n\n"
                    f"POST: {texto_post}\n\n"
                    "REGRAS CRÍTICAS:\n"
                    "1. Descreva uma METÁFORA VISUAL do tema, nunca o tema literal.\n"
                    "2. PROIBIDO: nomes próprios, siglas, números, CVEs, marcas, "
                    "aspas, código, nomes de ferramentas.\n"
                    "3. PROIBIDO citar objetos que carregam escrita: tela, monitor, "
                    "terminal, dashboard, placa, cartaz, livro, documento, teclado "
                    "com teclas visíveis, logotipo.\n"
                    "4. Use apenas formas, objetos, materiais, luz, cor e composição.\n"
                    "5. No máximo 35 palavras, em uma única frase.\n"
                    "6. RESPONDA APENAS COM A DESCRIÇÃO, sem introduções."
                ),
            ),
        )
    except Exception as e:
        print(f"⚠️ Cena visual indisponível ({e}). Usando a cena padrão.")
        return CENA_PADRAO

    cena = _higienizar_cena((resp.text or "").strip()) if resp else ""
    if len(cena) < 15:  # a higienização esvaziou a cena — não vale a pena arriscar
        return CENA_PADRAO
    return cena


def _prompt_visual(cena):
    return f"{cena}. {ESTILO_MARCA} {SEM_TEXTO}"


def _mime_da_imagem(dados):
    return "image/jpeg" if dados[:2] == b"\xff\xd8" else "image/png"


def imagem_tem_texto(path):
    """True se a imagem contiver tipografia legível. Salvaguarda, nunca bloqueio:
    se a própria verificação falhar, devolve False e o post segue."""
    if not IMG_TEXT_CHECK:
        return False
    try:
        from google.genai import types
        with open(path, "rb") as f:
            dados = f.read()
        resp = _gemini_com_retry(
            "Gemini (verificação de texto)",
            lambda: client.models.generate_content(
                model=TEXT_MODEL,
                contents=[
                    types.Part.from_bytes(data=dados, mime_type=_mime_da_imagem(dados)),
                    "Esta imagem contém letras, palavras, números ou qualquer "
                    "tipografia legível, mesmo que borrada ou parcial? "
                    "Responda apenas SIM ou NAO.",
                ],
            ),
            # 2 tentativas: o laço de seeds em gerar_imagem_pollinations já dá
            # outra chance, e cada retry aqui consome a cota de 5 req/min
            tentativas=2,
        )
        return (resp.text or "").strip().upper().startswith("SIM")
    except Exception as e:
        print(f"⚠️ Verificação de texto indisponível ({e}). Seguindo com a imagem.")
        return False


def gerar_imagem_pollinations(cena, titulo):
    """Gera a imagem por IA via Pollinations.ai — gratuito e sem API key.
    A cena vem do post, então a imagem varia a cada publicação. Se sair com
    texto, refaz com outro seed. Retorna o caminho salvo ou None se falhar."""
    import urllib.parse
    prompt = _prompt_visual(cena)
    for tentativa in range(1, IMG_RETRY_SEEDS + 1):
        # o seed varia por post E por tentativa, senão o retry repetiria a imagem
        seed = int(hashlib.md5(f"{titulo}:{tentativa}".encode("utf-8")).hexdigest(), 16) % 100000
        url = (
            "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
            + f"?width=1200&height=627&nologo=true&model={POLLINATIONS_MODEL}&seed={seed}"
        )
        try:
            r = requests.get(url, timeout=90)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"⚠️ Pollinations indisponível ({e}).")
            return None

        ctype = r.headers.get("Content-Type", "")
        if not ctype.startswith("image/") or len(r.content) < 2000:
            print(f"⚠️ Pollinations não retornou imagem (ctype='{ctype}', {len(r.content)} bytes).")
            return None

        with open(IMG_OUT, "wb") as f:
            f.write(r.content)

        if not imagem_tem_texto(IMG_OUT):
            print(f"🎨 Imagem gerada por IA grátis (Pollinations/{POLLINATIONS_MODEL}).")
            return IMG_OUT
        print(f"⚠️ Texto detectado na imagem ({tentativa}/{IMG_RETRY_SEEDS}); refazendo com outro seed.")

    print("⚠️ Pollinations insistiu em gerar texto. Passando ao próximo gerador.")
    return None


def gerar_imagem(texto, titulo, subtitulo):
    """Imagem do post: tenta IA grátis (Pollinations) e, se houver billing, o
    Gemini (Imagen/Flash Image); senão gera um CARD dinâmico com o tema do post;
    e por último o banner fixo. A arte por IA nasce de uma cena derivada do texto."""
    cena = descrever_cena(texto)
    print(f"🎬 Cena visual: {cena}")
    prompt = _prompt_visual(cena)

    # 0) Pollinations.ai — IA gratuita, sem API key (fonte primária)
    if USE_POLLINATIONS:
        img = gerar_imagem_pollinations(cena, titulo)
        if img:
            return img

    # 1) Imagen via predict (requer plano pago)
    try:
        from google.genai import types
        resp = _gemini_com_retry(
            "Gemini (imagem/Imagen)",
            lambda: client.models.generate_images(
                model=IMAGE_MODEL,
                prompt=prompt,
                config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="16:9"),
            ),
            tentativas=RETRY_MAX_IMG,
        )
        data = resp.generated_images[0].image.image_bytes
        with open(IMG_OUT, "wb") as f:
            f.write(data)
        print(f"🎨 Imagem gerada por IA ({IMAGE_MODEL}).")
        return IMG_OUT
    except Exception as e:
        print(f"⚠️ Imagen indisponível ({e}).")

    # 2) Modelo de imagem via generateContent (ex.: nano-banana) — pode estar no free tier
    try:
        resp = _gemini_com_retry(
            "Gemini (imagem/generateContent)",
            lambda: client.models.generate_content(model=IMAGE_MODEL_ALT, contents=prompt),
            tentativas=RETRY_MAX_IMG,
        )
        for part in resp.candidates[0].content.parts:
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                with open(IMG_OUT, "wb") as f:
                    f.write(inline.data)
                print(f"🎨 Imagem gerada por IA ({IMAGE_MODEL_ALT}).")
                return IMG_OUT
        print("⚠️ Modelo de imagem não retornou bytes de imagem.")
    except Exception as e:
        print(f"⚠️ Geração via generateContent indisponível ({e}).")

    # 3) Card dinâmico (varia por post)
    card = gerar_card(titulo, subtitulo)
    if card:
        return card

    # 4) Banner fixo (último recurso)
    print("🖼️ Usando banner de marca (fallback final).")
    return FALLBACK_IMG if os.path.exists(FALLBACK_IMG) else None


# ───────────────────────── LinkedIn: imagem + post ─────────────────────────
def upload_imagem(urn, path):
    """Images API: initializeUpload -> PUT binário -> retorna o image URN."""
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "LinkedIn-Version": LI_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }
    init = requests.post(
        "https://api.linkedin.com/rest/images?action=initializeUpload",
        headers=headers,
        json={"initializeUploadRequest": {"owner": urn}},
        timeout=20,
    )
    init.raise_for_status()
    value = init.json()["value"]
    upload_url = value["uploadUrl"]
    image_urn = value["image"]

    with open(path, "rb") as f:
        up = requests.put(
            upload_url,
            data=f.read(),
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
            timeout=60,
        )
    up.raise_for_status()
    return image_urn


def _escapar_commentary(texto):
    """Escapa os caracteres que quebram o parsing do campo commentary (Posts API),
    preservando # e @ para hashtags/menções continuarem funcionando."""
    for ch in ["\\", "(", ")", "[", "]", "{", "}", "<", ">"]:
        texto = texto.replace(ch, "\\" + ch)
    return texto


def postar(urn, texto, image_urn):
    """Publica via Posts API (/rest/posts) com imagem anexada."""
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "LinkedIn-Version": LI_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }
    payload = {
        "author": urn,
        "commentary": _escapar_commentary(texto),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if image_urn:
        payload["content"] = {"media": {"id": image_urn, "altText": "Anotação de Segurança da Informação"}}

    if DRY_RUN:
        print("🧪 DRY_RUN ativo — payload montado, NÃO publicando:")
        print(payload)
        return None
    return requests.post("https://api.linkedin.com/rest/posts", headers=headers, json=payload, timeout=30)


# ───────────────────────── Fluxo principal ─────────────────────────
def main():
    if not GEMINI_KEY or not ACCESS_TOKEN:
        print("❌ Secrets ausentes. Verifique GEMINI_API_KEY e LINKEDIN_ACCESS_TOKEN.")
        sys.exit(1)

    urn = get_my_urn()
    if not urn:
        # se foi expiração de token, a flag já foi criada para o workflow avisar
        sys.exit(1)

    arquivo, idx = carregar_proximo_arquivo()
    if not arquivo:
        print("📁 Nenhum arquivo .md encontrado para postar.")
        return

    print(f"📖 Lendo arquivo: {arquivo}")
    with open(arquivo, "r", encoding="utf-8") as f:
        conteudo = f.read()

    try:
        texto = gerar_texto(conteudo)
    except Exception as e:
        print(f"❌ Erro no Gemini (texto): {e}")
        sys.exit(1)
    if not texto:
        print("❌ Gemini retornou texto vazio.")
        sys.exit(1)

    titulo, subtitulo = _titulo_legivel(arquivo)
    imagem = gerar_imagem(texto, titulo, subtitulo)

    if DRY_RUN:
        # o upload já cria o asset no LinkedIn: num ensaio, deixaria uma imagem
        # órfã a cada execução
        print(f"🧪 DRY_RUN ativo — imagem em {imagem}, upload NÃO enviado.")
        image_urn = None
    else:
        try:
            image_urn = upload_imagem(urn, imagem) if imagem else None
        except requests.RequestException as e:
            print(f"⚠️ Falha no upload da imagem ({e}). Publicando sem imagem.")
            image_urn = None

    res = postar(urn, texto, image_urn)
    if DRY_RUN:
        return

    if res.status_code in (200, 201):
        print("🚀 Post publicado com sucesso!")
        with open(INDEX_FILE, "w") as f:
            f.write(str(idx + 1))
    elif res.status_code == 401:
        sinalizar_token_expirado(f"posts 401: {res.text[:200]}")
        sys.exit(1)
    else:
        print(f"❌ Erro LinkedIn ({res.status_code}): {res.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
