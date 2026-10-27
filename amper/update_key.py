"""Cheile publice Ed25519 cu care sunt semnate versiunile Amper (GitHub Releases).

Pi-ul instalează NUMAI arhive al căror `manifest.json` e semnat cu una dintre cheile de mai jos.
Cheia privată pereche stă doar ca secret în GitHub (AMPER_SIGNING_KEY), niciodată în repo.
O cheie nouă se generează cu `python tools/release.py keygen`; lista permite schimbarea cheii (se adaugă
cea nouă, se lansează o versiune semnată cu cea veche, apoi se semnează cu cea nouă).
"""

PUBLIC_KEYS: list[str] = [          # base64, 32 de octeți fiecare
    "yt6lXvC7qWeVWVign02N5znzBxdU01/E40Bfirlcw1k=",      # cheia autorului, 2026-10-27
]
