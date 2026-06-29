# Link Finder MCP

*🇬🇧 [English version](./README.md)*

Un serveur [MCP](https://modelcontextprotocol.io) pour l'[API Link Finder](https://app.link-finder.net) — trouvez des opportunités de backlinks, analysez vos concurrents, découvrez des domaines similaires grâce aux embeddings IA et gérez vos projets de prospection directement depuis Claude, ChatGPT, Cursor ou n'importe quel client MCP.

Les secrets passent uniquement par des variables d'environnement, et le serveur est agnostique de l'hébergement : exécutez-le en local via stdio, ou déployez-le n'importe où (Render ou n'importe quelle VM) avec un point d'accès HTTP/SSE protégé par un token bearer.

---

## Fonctionnalités

Tous les points d'accès de l'API Link Finder v2 sont exposés sous forme d'outils :

| Outil | Endpoint | Plan | Rôle |
| --- | --- | --- | --- |
| `get_account` | `getAccount` | Booster | Plan, crédits restants, fonctionnalités disponibles |
| `list_platforms` | `listPlatforms` | Booster | Plateformes de netlinking prises en charge |
| `list_locations` | `listLocations` | Booster | Pays/zones pour la recherche par mots-clés |
| `keyword_search` | `kwSearch` | Booster | Trouver des opportunités à partir de mots-clés (analyse SERP) |
| `competitor_analysis` | `competitor` | Booster | Domaines référents d'un concurrent disponibles à l'achat |
| `ai_search` | `aiSearch` | Booster | Prospection IA avec score de pertinence |
| `similar_domains` | `similarDomains` | Booster | Domaines similaires via embeddings IA (le détecteur de pépites) |
| `create_project` | `createProject` | Booster | Créer un projet |
| `list_projects` | `listProjects` | Booster | Lister les projets avec leurs compteurs |
| `project_favorites` | `projectFavorites` | Booster | Favoris d'un projet avec toutes les métriques |
| `add_favorite` | `addFavorite` | Booster | Ajouter / retirer un domaine d'un projet |
| `update_note` | `updateNote` | Booster | Annoter un favori remarquable |
| `check_domain` | `checkDomain` | API | Vérifier un domaine sur toutes les plateformes |
| `bulk_check` | `bulk` | API | Vérifier jusqu'à 50 000 domaines d'un coup |
| `get_search_history` | _local_ | — | Lire l'historique des recherches sauvegardé localement |

Plus un **prompt** guidé `backlink_workflow` qui déroule l'interview et le flux de prospection étape par étape.

Le serveur respecte aussi les bonnes pratiques de l'API : chaque résultat de recherche est **sauvegardé localement** dans un dossier `data/` et journalisé dans `data/searchHistory.json`, ce qui permet aux agents d'éviter les recherches en double qui gaspillent des crédits.

---

## Prérequis

- Python 3.10+
- Une clé API Link Finder — disponible dans votre compte sur <https://app.link-finder.net/account/> (plan Booster ou supérieur ; `checkDomain` et `bulk` nécessitent le plan API)

---

## Installation

```bash
git clone https://github.com/<vous>/link-finder-mcp.git
cd link-finder-mcp

python -m venv .venv && source .venv/bin/activate    # optionnel mais recommandé
pip install -r requirements.txt
```

Copiez le fichier d'environnement d'exemple et renseignez votre clé :

```bash
cp .env.example .env
# puis éditez .env et renseignez LINK_FINDER_API_KEY
```

> **Aucune clé dans le code.** La clé API est lue uniquement depuis `LINK_FINDER_API_KEY` et n'est jamais acceptée comme argument d'outil : elle ne peut donc pas fuiter via le contexte du modèle.

---

## Configuration

Toute la configuration se fait via des variables d'environnement :

| Variable | Requise | Défaut | Description |
| --- | --- | --- | --- |
| `LINK_FINDER_API_KEY` | oui | — | Votre clé API Link Finder |
| `MCP_TRANSPORT` | non | `stdio` | `stdio` (local), `http` (Streamable HTTP, recommandé pour l'hébergement) ou `sse` (ancien) |
| `MCP_BEARER_TOKEN` | hébergé seulement | — | Secret partagé que les clients envoient via `Authorization: Bearer <token>` |
| `PORT` | non | `8000` | Port d'écoute en mode hébergé (Render/Railway/Fly l'injectent) |
| `HOST` | non | `0.0.0.0` | Adresse d'écoute en mode hébergé |
| `MCP_STATELESS_HTTP` | non | `true` | Streamable HTTP uniquement. Sans état = pas d'état serveur par session ; robuste derrière les proxys/load balancers |
| `MCP_JSON_RESPONSE` | non | `false` | Streamable HTTP uniquement. `true` renvoie du JSON brut au lieu de réponses au format SSE (seulement si votre client l'exige) |
| `LINK_FINDER_DATA_DIR` | non | `data` | Où sont sauvegardés résultats + historique (vide = désactivé) |
| `LINK_FINDER_BASE_URL` | non | `https://app.link-finder.net/api/v2` | Surcharger l'URL de base de l'API |
| `LINK_FINDER_HTTP_TIMEOUT` | non | `120` | Timeout HTTP en secondes |
| `MCP_ALLOWED_HOSTS` | non | _(vide)_ | Liste d'hôtes autorisés (séparés par des virgules) pour la protection anti-DNS-rebinding. Vide = désactivée (fonctionne derrière n'importe quel proxy). Supporte un joker de port `:*`. |
| `MCP_ALLOWED_ORIGINS` | non | _(vide)_ | Liste d'origines autorisées (utilisée avec la précédente). |

### Quel transport choisir ?

- **Clients locaux (Claude Desktop, Cursor, ...)** → `stdio`.
- **Hébergé (Render ou n'importe quelle VM)** → `http` (**Streamable HTTP**). C'est le transport recommandé, compatible avec les proxys ; le point d'accès est sur **`/mcp`**.
- `sse` est l'ancien transport (point d'accès `/sse`). Il fonctionne, mais les flux SSE longue durée peuvent être mis en tampon ou coupés par les proxys des hébergeurs, ce qui peut bloquer la phase d'initialisation MCP. Préférez `http` sauf si votre client ne parle que le SSE.

---

## Connectez-le à votre chat IA — choisissez votre configuration

Il y a deux façons d'utiliser ce serveur. Choisissez selon votre application de chat :

| | **A. Local (sur votre ordinateur)** | **B. Hébergé (URL en ligne)** |
| --- | --- | --- |
| **Idéal pour** | Claude Desktop, Cursor, Cline et autres apps bureau | ChatGPT, Claude (web), ou tout chat qui se connecte à une URL MCP distante |
| **Fonctionnement** | L'app de chat lance le serveur pour vous | Vous déployez une fois (ex. Render), puis collez une URL + un token |
| **Transport** | `stdio` | `http` (Streamable HTTP) sur `/mcp` |
| **Mise en place** | [Claude Desktop](#a-utilisation-avec-claude-desktop-local) · [Cursor](#a-utilisation-avec-cursor-local) | [Déployer](#déploiement-sur-render-ou-nimporte-quelle-vm) puis [ChatGPT](#b-utilisation-avec-chatgpt-hébergé) · [tout client](#b-utilisation-avec-nimporte-quel-autre-chat-ia--client-mcp-hébergé) |

> Règle simple : **app bureau → A (local)**, **chat web/cloud → B (hébergé)**.

---

## Exécution en local (stdio)

```bash
export PYTHONPATH=src
python -m link_finder_mcp.server
```

Ou déboguez en interactif avec l'Inspecteur MCP :

```bash
PYTHONPATH=src mcp dev src/link_finder_mcp/server.py
```

---

## A. Utilisation avec Claude Desktop (local)

Éditez la configuration de Claude :

- macOS : `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows : `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "link-finder": {
      "command": "python",
      "args": ["-m", "link_finder_mcp.server"],
      "env": {
        "PYTHONPATH": "/chemin/absolu/vers/link-finder-mcp/src",
        "MCP_TRANSPORT": "stdio",
        "LINK_FINDER_API_KEY": "votre_cle_api_link_finder_ici",
        "LINK_FINDER_DATA_DIR": "/chemin/absolu/vers/link-finder-mcp/data"
      }
    }
  }
}
```

Redémarrez Claude Desktop. Les outils Link Finder apparaissent sous l'icône outils (le marteau). Essayez :

> « Vérifie mes crédits Link Finder, puis trouve des opportunités de backlinks françaises pour les mots-clés `assurance auto;comparateur assurance` avec un DR 20+ et 500+ de trafic. Sauvegarde les meilleurs dans un nouveau projet appelé *Assurance Q3*. »

Claude enchaînera `get_account` → `keyword_search` → `create_project` → `add_favorite`, puis proposera `similar_domains` sur les meilleurs résultats.

> Astuce : dans Claude Desktop, vous pouvez aussi attacher le prompt **`backlink_workflow`** (menu « + » / prompts) pour lancer l'interview guidée complète.

---

## A. Utilisation avec Cursor (local)

Ajoutez ceci à `~/.cursor/mcp.json` (ou au `.cursor/mcp.json` du projet) :

```json
{
  "mcpServers": {
    "link-finder": {
      "command": "python",
      "args": ["-m", "link_finder_mcp.server"],
      "env": {
        "PYTHONPATH": "/chemin/absolu/vers/link-finder-mcp/src",
        "LINK_FINDER_API_KEY": "votre_cle_api_link_finder_ici"
      }
    }
  }
}
```

---

## B. Utilisation avec ChatGPT (hébergé)

ChatGPT prend en charge les serveurs MCP distants (mode Développeur / connecteurs personnalisés et l'API Responses avec des `tools` de type `mcp`). Pour cela, le serveur doit être accessible en HTTPS avec un token bearer — voir d'abord [Déploiement sur Render](#déploiement-sur-render-ou-nimporte-quelle-vm).

### Option A — Mode Développeur / Connecteurs ChatGPT (interface)

1. Déployez le serveur (ex. sur Render) avec `MCP_TRANSPORT=http` et un `MCP_BEARER_TOKEN` solide.
2. Dans ChatGPT : **Réglages → Connecteurs → Avancé → Mode développeur**, puis **Créer** un connecteur.
3. Indiquez l'URL du point d'accès MCP de votre déploiement, ex. `https://votre-app.onrender.com/mcp`.
4. Ajoutez un en-tête `Authorization` : `Bearer <votre MCP_BEARER_TOKEN>`.
5. Enregistrez, activez le connecteur dans une conversation et demandez-lui de trouver des backlinks.

### Option B — API Responses d'OpenAI (par code)

```python
from openai import OpenAI

client = OpenAI()

resp = client.responses.create(
    model="gpt-4.1",
    tools=[
        {
            "type": "mcp",
            "server_label": "link-finder",
            "server_url": "https://votre-app.onrender.com/mcp",
            "headers": {"Authorization": "Bearer VOTRE_MCP_BEARER_TOKEN"},
            "require_approval": "never",
        }
    ],
    input="Utilise Link Finder pour trouver des opportunités de backlinks "
          "espagnoles (langue 2724) pour 'hosting wordpress' avec TF 15+ et "
          "présente un tableau.",
)

print(resp.output_text)
```

---

## B. Utilisation avec n'importe quel autre chat IA / client MCP (hébergé)

La plupart des autres clients (Claude sur le web, n8n, applications maison, SDK MCP, ...) se connectent à un serveur MCP distant de la même manière : une **URL** + un **token bearer**. Après le [déploiement](#déploiement-sur-render-ou-nimporte-quelle-vm) :

```json
{
  "url": "https://votre-app.onrender.com/mcp",
  "headers": { "Authorization": "Bearer VOTRE_MCP_BEARER_TOKEN" }
}
```

- **URL** → votre déploiement + `/mcp` (Streamable HTTP). Utilisez `/sse` seulement si votre client parle l'ancien transport SSE.
- **Token** → exactement la valeur définie dans `MCP_BEARER_TOKEN`.

C'est tout ce dont un client MCP conforme a besoin. Une fois connecté, demandez simplement en langage naturel (ex. *« trouve des opportunités de backlinks pour mon blog café en France »*) et le modèle appellera les bons outils.

---

## Déploiement sur Render (ou n'importe quelle VM)

Le serveur est agnostique de l'hébergement. En mode hébergé, il écoute sur `0.0.0.0:$PORT` et protège les points d'accès MCP avec un token bearer. Le transport hébergé recommandé est **Streamable HTTP** (`MCP_TRANSPORT=http`), servi sur **`/mcp`**.

Un fichier [`render.yaml`](./render.yaml) prêt à l'emploi est inclus :

```yaml
services:
  - type: web
    name: link-finder-mcp
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: python -m link_finder_mcp.server
    envVars:
      - key: PYTHONPATH
        value: src
      - key: MCP_TRANSPORT
        value: http
      - key: LINK_FINDER_API_KEY
        sync: false
      - key: MCP_BEARER_TOKEN
        sync: false
```

1. Poussez ce dépôt sur GitHub.
2. Dans Render : **New → Blueprint**, pointez-le vers le dépôt.
3. Renseignez les deux variables secrètes (`LINK_FINDER_API_KEY`, `MCP_BEARER_TOKEN`) dans le tableau de bord.
4. Déployez. Votre point d'accès MCP sera `https://<service>.onrender.com/mcp`.

Cela fonctionne pareil sur n'importe quelle VM / PaaS — définissez les variables d'environnement et lancez `python -m link_finder_mcp.server`. Mettez `MCP_TRANSPORT=sse` (point d'accès `/sse`) seulement si votre client exige l'ancien transport SSE.

Pointez votre client vers le point d'accès `/mcp` avec le token bearer :

```json
{
  "url": "https://votre-app.onrender.com/mcp",
  "headers": { "Authorization": "Bearer VOTRE_MCP_BEARER_TOKEN" }
}
```

> **Astuce pour générer un token solide :** `openssl rand -hex 32`.

> **Note sur les données sauvegardées :** sur les hébergeurs éphémères (comme le disque par défaut de Render), le dossier `data/` n'est pas persistant. Montez un disque persistant, ou pointez `LINK_FINDER_DATA_DIR` vers un chemin monté, si vous voulez que l'historique survive aux redémarrages. En local (stdio), la persistance est normale.

### Dépannage

- **`Failed to validate request: Received request before initialization was complete`** (en boucle, en SSE) — la phase d'initialisation MCP bloque. Avec l'ancien transport SSE, la réponse d'`initialize` revient via le flux longue durée `GET /sse`, et les proxys des hébergeurs (Render inclus) mettent souvent ce flux en tampon ou le coupent, si bien qu'il n'atteint jamais le client. **Solution :** utilisez `MCP_TRANSPORT=http` (Streamable HTTP, point d'accès `/mcp`), qui ne dépend pas d'un flux persistant et tourne sans état par défaut.
- **`SSE error: Non-200 status code (421)` / `Invalid Host header`** — c'est la protection anti-DNS-rebinding qui rejette le nom d'hôte public du proxy. Le serveur désactive la vérification d'hôte par défaut (le token bearer protège déjà l'accès), donc un déploiement neuf fonctionne directement. Si vous définissez `MCP_ALLOWED_HOSTS`, assurez-vous d'y inclure votre hôte public, ex. `votre-app.onrender.com`.
- **`GET / → 404` / `POST <chemin> → 405` dans les logs** — sans gravité. Chaque transport répond sur son propre chemin (`/mcp` pour Streamable HTTP, `/sse` + `/messages/` pour SSE) ; les sondes qui touchent d'autres chemins/méthodes sont attendues. Pointez votre client vers le bon chemin selon votre transport.

---

## Fonctionnement des crédits

- Les crédits sont partagés entre l'application web, l'extension navigateur et l'API.
- `keyword_search` coûte 1 crédit `keywords_search` **par mot-clé** ; `competitor_analysis` 1 par requête ; `ai_search` 1 par requête ; `similar_domains` 1 par domaine (ou par recherche de projet).
- Les crédits ne sont consommés que lorsque des résultats sont trouvés.
- Appelez toujours `get_account` en premier pour vérifier les crédits restants et les fonctionnalités débloquées par votre plan.

## Lire les résultats

Chaque résultat de domaine contient des champs sur lesquels filtrer et trier : `title`, `domain`, `dr` (Ahrefs), `tf`/`cf` (Majestic), `rd`, `traffic`, `ttf0` (thématique), `ai_lang`, `gg_news`, et les prix par plateforme (`-2` = introuvable, `-1` = prix indisponible, `>0` = prix dans la devise choisie). Chaque plateforme a aussi un champ `_url` avec le lien d'achat direct, et `best_price_platform` indique la moins chère.

---

## Licence

MIT — voir [LICENSE](./LICENSE).
