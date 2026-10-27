"""
`py3dtiles` com o LAZ lido pelo backend Lazrs (uma thread), no lugar do LazrsParallel que o laspy escolhe sozinho.

O py3dtiles 12 abre o LAZ no processo principal e depois cria os workers por fork. Com o LazrsParallel, o pool de
threads do lazrs não sobrevive ao fork: o worker trava no primeiro pedaço e o principal fica em espera ativa para
sempre (visto no T0, 27/10/2026, com py3dtiles 12.1.1, laspy 2.7.0 e lazrs 0.8.2). O paralelismo vem dos workers
(`--jobs`), então nada se perde.

Uso: o mesmo do `py3dtiles` (a imagem expõe como `py3dtiles-serial`).
"""

from __future__ import annotations

import sys

import laspy

_abre = laspy.open


def _abre_lazrs(*args, **kwargs):
    kwargs.setdefault("laz_backend", laspy.LazBackend.Lazrs)
    return _abre(*args, **kwargs)


def main() -> int:
    laspy.open = _abre_lazrs
    from py3dtiles.command_line import main as py3dtiles_main

    sys.argv[0] = "py3dtiles"
    return py3dtiles_main()


if __name__ == "__main__":
    sys.exit(main())
