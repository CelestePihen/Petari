# Kamek runtime loader — intégration Super Mario Galaxy

`kamekLoader.cpp` et `kamekLoader.h` fournissent le loader générique Kamek : relocations, BSS, constructeurs et synchronisation des caches. Leur code upstream est conservé sans modification.

Le bootstrap propre à Super Mario Galaxy se trouve dans [`loader/ArchipelagoBootstrap.cpp`](../../../loader/ArchipelagoBootstrap.cpp). Il réserve la mémoire permanente, charge le module régional et appelle `loadKamekBinary()` avant la création des heaps du jeu.

Le build est piloté depuis la racine du projet :

```sh
./build.sh RMGP01
./build.sh RMGE01
```

Voir le [guide Archipelago Kamek](../../../docs/archipelago-kamek.md) pour la préparation du jeu, les sorties et les tests. Les exemples upstream destinés à d’autres jeux ne font pas partie de cette copie.
