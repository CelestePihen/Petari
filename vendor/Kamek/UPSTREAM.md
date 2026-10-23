Source: https://github.com/Treeki/Kamek
Commit: b77b540a72c5ee8d8133a73e39fa25452aa42852
Local changes:
- Respect ELF alignment up to 32 bytes; expose _f_bss and _e_bss.
- Omit the upstream New Super Mario Bros. Wii example bootstrap and build scripts.
- Adapt loader/README.md to point to this project's SMG bootstrap and build guide.

The runtime loader is unchanged. See LICENSE for the upstream license.
Dynamic allocation contract: base aligned to 32 bytes; stronger ELF alignment is rejected.
