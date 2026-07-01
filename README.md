<!-- ══════════════════════ IDIOMAS / LANGUAGES ══════════════════════ -->
<div align="center">
<a href="README.md"><img src="https://img.shields.io/badge/Português-1987F0?style=for-the-badge" alt="Português"/></a>
<a href="README.en.md"><img src="https://img.shields.io/badge/English-555555?style=for-the-badge" alt="English"/></a>
<a href="README.es.md"><img src="https://img.shields.io/badge/Español-555555?style=for-the-badge" alt="Español"/></a>
</div>

<!-- ══════════════════════════ BANNER ══════════════════════════ -->
<div align="center">
  <img src="https://file.loading.io/color/feature/thumb/Blues-8.png?" width="100%" height="10px" alt="divider"/>
</div>

<div align="center">
  <img src="https://www.pulsetechnology.com/hs-fs/hubfs/Cybersecurity%20Graphic.gif?width=1600&height=511&name=Cybersecurity%20Graphic.gif" width="100%" alt="Automation Banner"/>
</div>

<div align="center">
  <img src="https://file.loading.io/color/feature/thumb/Blues-8.png?" width="100%" height="10px" alt="divider"/>
</div>

<br/>

<h1 align="center">Automação de Posts no LinkedIn</h1>
<p align="center"><em>Um post por dia no LinkedIn, 100% autônomo — do Markdown à publicação, sem servidor e sem custo</em></p>
<p align="center"><strong>Anotação .md → Gemini reescreve → imagem por IA → LinkedIn API</strong></p>

<div align="center">

<img src="https://img.shields.io/badge/Status-Publicando_diariamente-2E7D32?style=for-the-badge" alt="status"/>
<img src="https://img.shields.io/badge/Foco-Zero_Infra_%7C_100%25_GitHub_Actions-1987F0?style=for-the-badge&logo=githubactions&logoColor=white" alt="foco"/>
<br/>
<img src="https://img.shields.io/badge/Python_3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="python"/>
<img src="https://img.shields.io/badge/GitHub_Actions-2088FF?style=flat-square&logo=githubactions&logoColor=white" alt="actions"/>
<img src="https://img.shields.io/badge/Google_Gemini-8E75B2?style=flat-square&logo=googlegemini&logoColor=white" alt="gemini"/>
<img src="https://img.shields.io/badge/LinkedIn_API-0A66C2?style=flat-square&logo=linkedin&logoColor=white" alt="linkedin"/>
<img src="https://img.shields.io/badge/Pollinations.ai-FF6F00?style=flat-square" alt="pollinations"/>
<img src="https://img.shields.io/badge/Pillow-11557C?style=flat-square&logo=python&logoColor=white" alt="pillow"/>

</div>

<!-- ══════════════════════════ NAVEGAÇÃO ══════════════════════════ -->
<div align="center">

<a href="#sobre"><img src="https://img.shields.io/badge/▸_SOBRE-1987F0?style=for-the-badge" alt="sobre"/></a>
<a href="#como-funciona"><img src="https://img.shields.io/badge/▸_COMO_FUNCIONA-000000?style=for-the-badge" alt="como"/></a>
<a href="#tecnologias"><img src="https://img.shields.io/badge/▸_TECNOLOGIAS-1987F0?style=for-the-badge" alt="tech"/></a>
<a href="#configuração"><img src="https://img.shields.io/badge/▸_CONFIGURAÇÃO-000000?style=for-the-badge" alt="config"/></a>
<a href="#uso"><img src="https://img.shields.io/badge/▸_USO-1987F0?style=for-the-badge" alt="uso"/></a>

</div>

<br/>

> 💡 **Zero infraestrutura.** Roda inteiramente no **GitHub Actions** (cron diário + disparo manual) — sem servidor, sem custo, sem intervenção manual. Aponte a variável `CONTENT_DIR` para qualquer pasta de arquivos `.md` e o motor passa a postar o seu conteúdo.

<!-- ══════════════════════════ SOBRE ══════════════════════════ -->
## Sobre

Publica **um post por dia no LinkedIn** de forma autônoma, direto do GitHub Actions. A ferramenta pega anotações em Markdown, **reescreve o texto com IA** (Google Gemini) num tom profissional e em português do Brasil, **gera uma imagem de capa por IA** (com uma cascata de fallbacks para nunca falhar) e **publica pela API oficial do LinkedIn** — tudo agendado por um cron.

Foi criada para divulgar diariamente as anotações da pós em Segurança da Informação, mas o motor é **genérico**: qualquer pasta de `.md` vira uma fila de posts.

<!-- ══════════════════════════ DESTAQUES ══════════════════════════ -->
## Destaques de Engenharia

| Recurso | O que faz |
|---|---|
| **Reescrita com IA** | Gemini (`gemini-2.5-flash`) transforma a anotação em post de LinkedIn — prompt força pt-BR, remove markdown/emojis e "cara de IA" |
| **Imagem por IA com fallback em cascata** | 4 estratégias para o post **sempre** sair com capa |
| **Resiliência a falhas transitórias** | Retry com *backoff* exponencial; distingue erro transitório (503/429 → tenta de novo) de permanente (cota/plano pago → cai no fallback) |
| **Estado versionado** | `post_index.txt` guarda o índice do próximo post; avança a cada publicação e é commitado pelo próprio workflow — impossível dessincronizar |
| **Gestão do token** | O access token do LinkedIn dura ~60 dias e não tem refresh; ao expirar, o workflow **abre uma Issue** de lembrete |
| **Modo `dry_run`** | Monta o post inteiro (texto + imagem + payload) e **não publica**, para validar com segurança |

**Cascata de imagem** (da melhor à mais resiliente — o post nunca sai sem capa):

1. **Pollinations.ai** — IA gratuita, sem API key (fonte primária)
2. **Imagen / Gemini Image** — quando há billing
3. **Card dinâmico com PIL** — gradiente e cor variando por post
4. **Banner fixo de marca** — `assets/post_fallback.png`

<!-- ══════════════════════════ COMO FUNCIONA ══════════════════════════ -->
## Como Funciona

```mermaid
flowchart TD
    A[Cron diário / disparo manual] --> B[Valida secrets e obtém a URN do LinkedIn]
    B --> C[Lê post_index.txt e escolhe o próximo .md em CONTENT_DIR]
    C --> D[Gemini reescreve o texto em pt-BR]
    D --> E{Gera imagem}
    E -->|1| P[Pollinations.ai grátis]
    E -->|2| I[Imagen / Gemini Image]
    E -->|3| K[Card dinâmico PIL]
    E -->|4| F[Banner fixo]
    P & I & K & F --> U[Upload da imagem + publica em /rest/posts]
    U -->|sucesso| G[Incrementa post_index.txt e faz commit]
    U -->|401 token| T[Cria flag .token_expired → workflow abre Issue]
```

**Fluxo detalhado:**

1. Valida os secrets e chama `GET /v2/userinfo` para montar a URN do autor (detecta token expirado via 401).
2. Lê `post_index.txt` e varre `CONTENT_DIR` (`os.walk`) em busca do próximo `.md` — índice **circular** (ao terminar a lista, recomeça).
3. Envia o conteúdo ao **Gemini**, que reescreve como post de LinkedIn (hook, bullets, CTA, 3 hashtags, ≤1300 caracteres, sem emojis/markdown).
4. Gera a imagem de capa pela **cascata de fallback**.
5. Faz o upload da imagem (`/rest/images` → `initializeUpload` → `PUT` binário) e publica em **`/rest/posts`**.
6. Em caso de sucesso, incrementa e commita `post_index.txt`.

<!-- ══════════════════════════ TECNOLOGIAS ══════════════════════════ -->
## Tecnologias

<div>
<img src="https://img.shields.io/badge/Python_3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="python"/>
<img src="https://img.shields.io/badge/GitHub_Actions-2088FF?style=flat-square&logo=githubactions&logoColor=white" alt="actions"/>
<img src="https://img.shields.io/badge/google--genai-8E75B2?style=flat-square&logo=googlegemini&logoColor=white" alt="genai"/>
<img src="https://img.shields.io/badge/LinkedIn_Posts_API-0A66C2?style=flat-square&logo=linkedin&logoColor=white" alt="linkedin"/>
<img src="https://img.shields.io/badge/Pollinations.ai-FF6F00?style=flat-square" alt="pollinations"/>
<img src="https://img.shields.io/badge/Pillow-11557C?style=flat-square&logo=python&logoColor=white" alt="pillow"/>
<img src="https://img.shields.io/badge/requests-2E7D32?style=flat-square" alt="requests"/>
</div>

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.11 |
| Orquestração | GitHub Actions (cron + `workflow_dispatch`) |
| Reescrita de texto | Google Gemini (`google-genai`) |
| Imagem por IA | Pollinations.ai · Imagen/Gemini Image |
| Imagem fallback | Pillow (PIL) |
| Publicação | LinkedIn Posts API (`/rest/posts`) |
| HTTP | `requests` |

Dependências em [`requirements.txt`](requirements.txt): `google-genai`, `requests`, `Pillow`.

<!-- ══════════════════════════ CONFIGURAÇÃO ══════════════════════════ -->
## Configuração

**1. Secrets (obrigatórios)** — em **Settings → Secrets and variables → Actions**:

| Secret | Onde obter |
|---|---|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/app/apikey) |
| `LINKEDIN_ACCESS_TOKEN` | [LinkedIn OAuth Token Generator](https://www.linkedin.com/developers/tools/oauth/token-generator) — escopos `openid`, `profile`, `w_member_social` |

**2. Variáveis opcionais (com defaults):**

| Variável | Default | Função |
|---|---|---|
| `CONTENT_DIR` | `content` | Pasta raiz varrida em busca dos `.md` |
| `GEMINI_TEXT_MODEL` | `gemini-2.5-flash` | Modelo de reescrita de texto |
| `USE_POLLINATIONS` | `1` | Usa o Pollinations.ai como fonte primária de imagem |
| `POLLINATIONS_MODEL` | `flux` | Modelo do Pollinations (`flux`, `turbo`, …) |
| `DRY_RUN` | `0` | `1` = monta tudo mas **não** publica |
| `LINKEDIN_VERSION` | `202606` | Versão da LinkedIn API |
| `GEMINI_RETRY_MAX` / `_IMG` / `_BASE` | `5` / `3` / `2.0` | Parâmetros do retry com backoff |

**3. Conteúdo** — coloque seus arquivos `.md` em `content/` (ou aponte `CONTENT_DIR` para outra pasta). Subpastas são varridas recursivamente.

<!-- ══════════════════════════ USO ══════════════════════════ -->
## Uso

**Automático:** o workflow roda diariamente às **20:23 UTC (~17:23 BRT)** — configurável em [`.github/workflows/post_diario.yml`](.github/workflows/post_diario.yml).

**Manual / teste (não publica):**
```bash
gh workflow run post_diario.yml --ref main -f dry_run=true
```

**Local (não publica):**
```bash
pip install -r requirements.txt
DRY_RUN=1 CONTENT_DIR=content \
GEMINI_API_KEY="..." LINKEDIN_ACCESS_TOKEN="..." \
python automacao_linkedin.py
```

<!-- ══════════════════════════ TOKEN ══════════════════════════ -->
## Sobre o Token do LinkedIn

Apps de perfil pessoal do LinkedIn **não emitem refresh token**, então o access token (validade ~60 dias) é usado diretamente. Quando expira, o script cria a flag `.token_expired` e o workflow **abre uma Issue** de lembrete — basta gerar um novo token e atualizar o secret `LINKEDIN_ACCESS_TOKEN`.

<!-- ══════════════════════════ LICENÇA ══════════════════════════ -->
## Licença

[MIT](LICENSE).

<div align="center">
  <img src="https://file.loading.io/color/feature/thumb/Blues-8.png?" width="100%" height="10px" alt="divider"/>
</div>

<p align="center"><sub>Desenvolvido por <strong><a href="https://github.com/douglascshun">Douglas Cshunderlick</a></strong> (r4bbi7) · Segurança da Informação · 2026</sub></p>
