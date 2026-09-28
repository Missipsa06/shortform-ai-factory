"""
Client MCP minimal, en JSON-RPC sur l'entrée et la sortie standard.

Sert à donner des outils au modèle pendant la rédaction du script, seule étape
du pipeline où l'aléatoire est déjà admis. Voir `content/agent.py`.

**Pourquoi pas le SDK `mcp`** : il est asynchrone, alors que le pipeline est
synchrone de bout en bout — `asyncio` n'apparaît que dans trois modules feuilles,
qui l'enferment derrière une façade bloquante. Le protocole tient ici en une
centaine de lignes de bibliothèque standard, contre une dépendance et une boucle
d'événements à faire cohabiter avec Airflow. Même raisonnement que pour LangChain.

**Pourquoi stdio et non HTTP** : un serveur de référence se lance en
sous-processus, vit le temps du bloc `with` et meurt avec lui. Aucun port à
réserver, aucun service à surveiller, rien à nettoyer si le pipeline échoue.

Usage :
    with ServeurMCP(["uvx", "mcp-server-fetch"], nom="fetch") as serveur:
        for outil in serveur.outils():
            ...
        texte = serveur.appeler("fetch", {"url": "https://exemple.fr"})
"""

import json
import logging
import subprocess
import threading
from typing import Any

logger = logging.getLogger(__name__)

PROTOCOLE = "2025-06-18"

# Un serveur muet ne doit pas suspendre une tâche Airflow indéfiniment. La valeur
# est large : `fetch` va chercher une page distante, ce qui peut être lent.
DELAI_DEFAUT_S = 90


class ErreurMCP(RuntimeError):
    """Échec de dialogue avec un serveur MCP."""


class ServeurMCP:
    """
    Un serveur MCP lancé en sous-processus, piloté en JSON-RPC sur stdio.

    N'est pas réentrant : un seul appel à la fois, ce qui suffit à une boucle
    d'agent séquentielle et évite d'avoir à apparier les identifiants de requête.
    """

    def __init__(self, commande: list[str], nom: str,
                 delai_s: int = DELAI_DEFAUT_S) -> None:
        self.commande = commande
        self.nom = nom
        self.delai_s = delai_s
        self._proc: "subprocess.Popen[str] | None" = None
        self._id = 0
        self._outils: "list[dict] | None" = None

    # ── Cycle de vie ─────────────────────────────────────────────────────────

    def __enter__(self) -> "ServeurMCP":
        self._proc = subprocess.Popen(
            self.commande,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        # La sortie d'erreur est drainée en continu : un serveur qui journalise
        # abondamment remplirait sinon le tampon du tube et se bloquerait, sans
        # que rien ne l'indique côté client.
        threading.Thread(target=self._drainer_erreurs, daemon=True).start()
        self._initialiser()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=10)
        except Exception:
            self._proc.kill()
        finally:
            self._proc = None

    def _drainer_erreurs(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for ligne in proc.stderr:
            ligne = ligne.strip()
            if ligne:
                logger.debug("[%s] %s", self.nom, ligne)

    # ── Transport ────────────────────────────────────────────────────────────

    def _ecrire(self, message: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise ErreurMCP(f"serveur {self.nom} non démarré")
        proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def _lire(self) -> dict:
        """
        Lit la prochaine réponse, en ignorant ce qui n'est pas du JSON-RPC.

        Certains serveurs écrivent des bannières sur la sortie standard avant de
        parler le protocole ; les filtrer ici évite de faire échouer la poignée
        de main sur du bruit.
        """
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise ErreurMCP(f"serveur {self.nom} non démarré")
        resultat: "list[dict]" = []

        def lecture() -> None:
            for ligne in proc.stdout:            # type: ignore[union-attr]
                ligne = ligne.strip()
                if ligne.startswith("{"):
                    resultat.append(json.loads(ligne))
                    return

        fil = threading.Thread(target=lecture, daemon=True)
        fil.start()
        fil.join(self.delai_s)
        if not resultat:
            raise ErreurMCP(
                f"serveur {self.nom} : aucune réponse en {self.delai_s} s"
            )
        return resultat[0]

    def _demander(self, methode: str, params: "dict | None" = None) -> Any:
        self._id += 1
        self._ecrire({"jsonrpc": "2.0", "id": self._id,
                      "method": methode, "params": params or {}})
        reponse = self._lire()
        if "error" in reponse:
            raise ErreurMCP(f"serveur {self.nom} : {reponse['error']}")
        return reponse.get("result", {})

    def _initialiser(self) -> None:
        infos = self._demander("initialize", {
            "protocolVersion": PROTOCOLE,
            "capabilities": {},
            "clientInfo": {"name": "shortform-ai-factory", "version": "1"},
        })
        self._ecrire({"jsonrpc": "2.0", "method": "notifications/initialized"})
        serveur = infos.get("serverInfo", {})
        logger.info("MCP %s : %s %s", self.nom,
                    serveur.get("name", "?"), serveur.get("version", ""))

    # ── Interface ────────────────────────────────────────────────────────────

    def outils(self) -> list[dict]:
        """Schémas des outils exposés, tels que le serveur les déclare."""
        if self._outils is None:
            self._outils = self._demander("tools/list").get("tools", [])
        return self._outils

    def appeler(self, outil: str, arguments: dict, max_car: int = 20_000) -> str:
        """
        Exécute un outil et rend son résultat en texte.

        Args:
            max_car: Plafond de caractères rendus. Un outil de récupération de
                     page peut renvoyer des centaines de milliers de caractères,
                     qui rempliraient la fenêtre de contexte en un seul appel et
                     feraient échouer la rédaction qui suit.
        """
        resultat = self._demander("tools/call",
                                  {"name": outil, "arguments": arguments})
        morceaux = [
            bloc.get("text", "")
            for bloc in resultat.get("content", [])
            if bloc.get("type") == "text"
        ]
        texte = "\n".join(m for m in morceaux if m)
        if resultat.get("isError"):
            logger.warning("MCP %s/%s a échoué : %s", self.nom, outil, texte[:200])
            return f"ERREUR : {texte[:500]}"
        if len(texte) > max_car:
            texte = texte[:max_car] + f"\n[…tronqué à {max_car} caractères]"
        return texte
