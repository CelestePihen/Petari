# Archipelago : première intégration Kamek SMG1

L’infrastructure Kamek est intégrée au projet. La commande `./build.sh REGION` construit le module indépendant et son bootstrap ; `./build.sh REGION --diagnostic` conserve le build Petari de diagnostic sans Archipelago dans le DOL principal. Les ISO et les dossiers de jeu existants ne sont pas modifiés. Le module contient un hook `StageResult` qui affiche le log d’étoile et publie son nom de galaxie et son ID dans la file de la mailbox v2. Le traitement des checks par un serveur Archipelago reste à implémenter.

**Chargement réel et communication Python validés dans Dolphin pour RMGP01 et RMGE01.** Les essais automatiques utilisent le backend vidéo Null de Dolphin : ils exécutent le jeu, son DVD, son SDK et ses heaps, mais ne valident pas le rendu, les changements de galaxie ou une partie complète. Les resets, savestates, Home Menu/RSO et la Wii réelle restent à vérifier.

## Construire et lancer

Prérequis Linux : les outils Petari déjà présents sous `build/` (`wibo`, compilateur `GC/3.0a5`), Python, .NET SDK 10 et Dolphin. Kamek est fourni en sources sous `vendor/Kamek`, au commit `b77b540a72c5ee8d8133a73e39fa25452aa42852`, avec le correctif d’alignement et les symboles de fin BSS. Aucun binaire CodeWarrior n’est redistribué.

Pour une nouvelle installation, préparer d’abord la toolchain Petari avec sa procédure habituelle (`python configure.py --version RMGP01`, puis `ninja tools`). Fournir les DOL et `product.sel` retail dans `orig/REGION/`, comme dans le projet existant.

```sh
./build.sh RMGP01
./build.sh RMGE01
```

Ces commandes compilent uniquement les sources de `src/Game/Archipelago/`, le petit bootstrap et Kamek si nécessaire. **Elles ne recompilent pas les sources du jeu.** `configure.py` reste réservé à Petari et ne relie plus Archipelago dans le DOL principal.

Pour préparer un jeu de test, fournir une première fois un dossier DATA extrait contenant `sys/` et `files/`. Le build crée une copie privée sous `build/kamek/REGION/game/DATA`, utilise des reflinks si disponibles et remplace uniquement ses propres fichiers de test. Ensuite, `./build.sh REGION` met aussi à jour cette copie si son marqueur de propriété et son DOL sont cohérents ; il reprend le chemin source enregistré lors de sa préparation. Garder ce dossier source accessible. Un dossier arbitraire ou un DOL modifié manuellement est refusé. Sans copie préparée, la commande construit seulement les artefacts. Avec `--game-output`, fournir à nouveau `--game-dir` et `--game-output` pour mettre à jour cet emplacement personnalisé.

```sh
./build.sh RMGP01 --game-dir /chemin/vers/DATA_PAL
./build.sh RMGE01 --game-dir /chemin/vers/DATA_USA
```

Ouvrir ensuite `build/kamek/REGION/game/DATA/sys/main.dol` depuis Dolphin, ou lancer :

```sh
dolphin-emu -e "$PWD/build/kamek/RMGP01/game/DATA/sys/main.dol"
```

Dolphin utilise alors ses paramètres, manettes et sauvegardes habituels. Le profil privé `--dolphin-user` ci-dessous sert uniquement aux tests automatiques et n’est pas requis pour charger Kamek.

Pour tester la communication DME en lançant une instance de test séparée :

```sh
python -m venv .venv
.venv/bin/pip install dolphin-memory-engine==1.3.1
.venv/bin/python test_mailbox.py --region RMGP01 \
  --launch build/kamek/RMGP01/game/DATA/sys/main.dol \
  --dolphin-user build/kamek/RMGP01/dolphin-user \
  --value 0x12345678 --observe-seconds 10
```

Reprendre la même commande avec `RMGE01` pour USA. `--null-video` active le backend Null utilisé pour les tests automatiques ; sous Linux, il demande quand même une session graphique xcb accessible. Le backend Qt `offscreen` est refusé par ce Dolphin.

Le script découvre la mailbox, vérifie son protocole, écrit `testValue`, relit la valeur et vérifie sa stabilité. Par défaut, il lit les événements sans les acquitter ; `--ack-events` autorise aussi la mise à jour de leur compteur de lecture. Il arrête **seulement l’instance qu’il a lancée** après le test. `--keep-running` la laisse ouverte ; `--read-only` n’écrit rien. Pour inspecter visuellement le démarrage avant cet arrêt, augmenter `--observe-seconds`.

Résultat attendu : `region` correcte, `version: 2`, et `written == readback == 305419896` pour `0x12345678`. L’adresse est découverte à chaque lancement ; ce n’est pas une constante du client. Avec `--output fichier.json`, le résultat est conservé.

**Attention aux dossiers existants sur cette machine :** lors des essais, `SMG-Archipelago-RMGP01/DATA/sys/boot.bin` identifiait un jeu **RMGE01**, et le dossier nommé RMGE01 identifiait **RMGP01**. Aucun dossier source n’a été renommé. Le build vérifie l’identifiant réel et refuse une association incorrecte.

## Permissions Linux

Aucun réglage du noyau, `setcap`, `sudo sysctl` ou désactivation de protection n’est effectué. Les tests ont réussi avec `kernel.yama.ptrace_scope=1`.

Le mode `--launch` fait de Dolphin un enfant du client Python, ce qui respecte cette règle Yama. Sous Linux, un lien d’exécutable privé et `DME_DOLPHIN_PROCESS_NAME` permettent à DME de sélectionner cette instance plutôt qu’un autre Dolphin ouvert. Cette variable est [prise en charge par DME](https://github.com/randovania/py-dolphin-memory-engine/blob/main/Source/DolphinProcess/Linux/LinuxDolphinProcess.cpp).

Pour une instance lancée indépendamment, DME peut repérer Dolphin tout en échouant à lire MEM1 avec Yama. Pour **lire la partie en cours sans la redémarrer**, utiliser le backend explicite de lecture seule de la mémoire partagée Linux :

```sh
pgrep -a -x dolphin-emu
.venv/bin/python test_mailbox.py --region RMGP01 --read-only \
  --shared-memory-pid <PID_AFFICHE> --observe-seconds 0
```

Remplacer `<PID_AFFICHE>` par le PID de l’instance souhaitée. Ce backend ouvre en `O_RDONLY` le fichier partagé de Dolphin via `/proc/PID/fd`, même s’il est déjà supprimé de `/dev/shm`. Il vérifie l’exécutable, le nom du fichier, son propriétaire, sa taille, son inode et la présence d’un mapping partagé MEM1 à l’offset zéro, puis applique les mêmes contrôles région/protocole/bornes du descripteur. Il ne dépend pas de DME, ne lit pas `/proc/PID/mem` et refuse toute écriture. Le fichier partagé est une interface propre à cette version Linux de Dolphin, dont la présence reste à vérifier sur d’autres distributions/versions. L’accès aux descripteurs peut aussi être refusé par les permissions de l’hôte.

Pour les **écritures DME**, utiliser le mode parent/enfant `--launch` ci-dessus. Aucun changement des protections Linux n’est nécessaire. En présence d’autres restrictions (conteneur, Flatpak, politique ptrace plus stricte), le script affiche l’échec et ne modifie pas le système.

Le diagnostic système confirme `process_vm_readv(...) = -1 EPERM` pour DME avec `ptrace_scope=1`.

La lecture seule de la mémoire partagée a été validée sur une session PAL le 23 octobre 2026, avec l’ancien protocole v1. Le protocole courant est v2 : une collecte publie désormais un événement sans modifier `testValue`.

## Écrire et déclarer un hook

Mettre les fichiers C++ dans `src/Game/Archipelago/` et les en-têtes dans `include/Game/Archipelago/`. Tous les `.cpp` sont compilés automatiquement, y compris ceux des sous-dossiers. Un changement de code ou une nouvelle déclaration ne nécessite aucune modification de `loader/ArchipelagoBootstrap.cpp`. Les configurations IntelliSense incluent `vendor/` et le dossier généré `build/kamek/RMGP01` ; remplacer ce dernier par `build/kamek/RMGE01` pour examiner les adresses USA dans l’éditeur. Ce choix ne change pas la région sélectionnée par `./build.sh`.

Le fichier [StarLog.cpp](../src/Game/Archipelago/StarLog.cpp) contient le hook courant. Voici sa forme minimale, avec uniquement le log et l’appel original :

```cpp
#include "Game/Archipelago/Hooks.hpp"
#include "Game/System/GameSequenceFunction.hpp"
#include "revolution/os.h"

namespace Archipelago {
    void onStageResult() {
        const char* stage = GameSequenceFunction::getClearedStageName();
        s32 star = GameSequenceFunction::getClearedPowerStarId();
        OSReport("[ARCHIPELAGO] Star collected: %s, ID: %d\n", stage, star);
        GameSequenceFunction::updateGameDataAndSequenceAfterStageResultSequence();
    }
}

AP_CALL_HOOK(StageResult, Archipelago::onStageResult);
```

`AP_CALL_HOOK` remplace un **appel** existant par un appel à cette fonction. Le wrapper doit avoir la même convention d’appel et la même signature que la fonction remplacée ; les méthodes membres exigent de prendre aussi en compte le pointeur `this`. Il doit appeler explicitement la fonction originale si son comportement doit être conservé. L’exemple ci-dessus conserve le comportement original et affiche son message juste avant l’appel, dans la branche `mMoveType == 4`.

Le nom `StageResult` est déclaré une seule fois dans [config/Archipelago/hooks.json](../config/Archipelago/hooks.json) :

```json
{
  "version": 1,
  "hooks": {
    "StageResult": {
      "owner": "requestGalaxyMove__20GameSequenceProgressFRC18GalaxyMoveArgument",
      "callee": "updateGameDataAndSequenceAfterStageResultSequence__20GameSequenceFunctionFv"
    }
  }
}
```

`owner` est le symbole de la fonction contenant l’appel ; `callee` est celui de la fonction appelée. Ces noms viennent des fichiers `config/REGION/symbols.txt`. Le build recherche exactement un `bl` entre ces deux fonctions dans le DOL retail régional. Un symbole inconnu, zéro ou plusieurs appels, deux noms désignant le même site ou un conflit avec les hooks du bootstrap font échouer le build.

Le build génère `build/kamek/REGION/ArchipelagoHookSites.generated.hpp` avec les adresses vérifiées : `0x803B5708` pour PAL et `0x803B56EC` pour USA dans cet exemple. Le fichier n’est pas à éditer. Une entrée de configuration seule n’installe rien : seule une déclaration C++ effectivement compilée produit un hook. Retirer la ligne `AP_CALL_HOOK` retire le hook au prochain build et redémarrage du jeu. Avec l’ancien compilateur, placer chaque déclaration de hook sur sa propre ligne.

Les commandes Kamek d’appel sont vérifiées après liaison : destination déclarée, absence de doublon, cible alignée dans le code du module. Les écritures arbitraires, les branches simples et les patches non déclarés restent refusés. Cette première interface ne fournit pas de remplacement d’entrée de fonction ni de trampoline au milieu d’une fonction.

Au démarrage, le bootstrap vérifie **toutes** les instructions originales avant de transmettre le module au loader. Celui-ci installe les appels, puis le bootstrap contrôle les instructions obtenues avant de publier la mailbox. Une instruction incompatible provoque l’erreur `12` ; une installation incorrecte provoque l’erreur `13`. Le manifeste de build décrit les hooks de démarrage dans `hooks` et les appels installés par le module dans `module_hooks`.

Pour vérifier les instructions réellement installées dans une partie Linux déjà ouverte :

```sh
pgrep -a -x dolphin-emu
.venv/bin/python test_mailbox.py --region RMGP01 --read-only \
  --shared-memory-pid <PID_AFFICHE> --observe-seconds 0 \
  --manifest build/kamek/RMGP01/manifest.json
```

Le résultat `hooks` contient les sites, les cibles dans le module et les instructions lues en mémoire. Utiliser le manifeste du même build que la partie chargée. Le même argument `--manifest` est disponible avec les modes DME et `--launch`.

## Contrat mémoire

Le DOL de départ est le retail identifié par son SHA-1 :

| Région | SHA-1 |
|---|---|
| PAL | `9bd1daa520bd1f573b6a4b82e03336e9c51b511f` |
| USA | `9a71008ae1ee9010e267fa67d1f0b0d4f0e895dd` |

Le bootstrap est placé à l’ancienne limite basse d’arène déduite des instructions d’initialisation de pile et du contrat LCF retail : fin de la pile principale + `0x2000` de pile de debug, arrondie à 32 octets. **Ce n’est pas une recherche de trou libre.**

Deux appels sont remplacés, avec vérification de la cible retail :

1. Dans `OSInit`, l’appel à `OSSetMEM1ArenaLo` passe par `apReserveArena`, qui exclut le bootstrap avant `ClearArena` et avant les allocations du jeu.
2. Dans `main`, l’appel à `HeapMemoryWatcher::createRootHeap` passe par `apLoadAndCreateRootHeap`, après `DVDInit` et `VIInit`. Celui-ci alloue le module avec `OSAllocFromMEM1ArenaLo`, charge le fichier, exécute le loader Kamek, puis appelle la fonction retail de création des root heaps.

L’allocation temporaire du fichier est la dernière allocation de l’arène avant la création des heaps. Sa borne est vérifiée avant de la restituer. Le module et le bootstrap restent exclus du root heap pour toute la session. Il n’y a ni allocation dans un heap de scène, ni adresse MEM2 supposée libre, ni buffer écrit dans un heap sans allocation.

La section ajoutée au DOL contient aussi les zéros du BSS du bootstrap ; le BSS et le point d’entrée **retail** restent identiques. Le build compare chaque section retail et n’accepte que les deux mots d’instruction déclarés. Le manifeste contient les adresses et instructions attendues/remplacées par région.

Le module utilise `-sdata 0 -sdata2 0`, les définitions de région Petari et les imports globaux de `config/REGION/symbols.txt`. Il ne change pas `r2/r13`. Le loader initialise le BSS, reloge les références, synchronise les caches et exécute les constructeurs. Un marqueur BSS mis à jour par un constructeur C++ doit être valide avant de publier la mailbox.

Les constructeurs s’exécutent avant la création des heaps du jeu : ils ne doivent pas appeler des services qui exigent ces heaps. Les hooks d’appel permettent de déclencher le code de gameplay à un point ultérieur adapté ; leur choix doit tenir compte des services disponibles à ce moment.

Le correctif de Kamek respecte les alignements ELF jusqu’à **32 octets** et rejette un besoin supérieur. L’allocateur fournit cette même garantie. Les fichiers C++ supplémentaires peuvent être ajoutés dans `src/Game/Archipelago/` ; le budget mémoire reste fini. La cible accepte les relocations internes, les imports MEM1 et les hooks d’appel déclarés et vérifiés. Les autres commandes de patch du module sont refusées.

Le bootstrap vérifie taille et empreinte FNV-1a du module avant de transmettre ses commandes au loader. Cette empreinte détecte un fichier périmé/corrompu ; **elle ne constitue pas une signature cryptographique**. Un nouveau module entraîne une reconstruction du petit bootstrap et du DOL de sortie par patch, toujours sans recompilation du jeu.

## Découverte de la mailbox

Le bootstrap possède un descripteur de 48 octets, aligné sur 4 :

| Mots u32 big endian | Contenu |
|---|---|
| 0–1 | `APBOOT01` |
| 2–3 | version de découverte `1`, taille `48` |
| 4–5 | identifiant disque à six caractères, complété de deux zéros |
| 6 | état : `0` initial, `1` réservé, `2` chargement, `3` prêt, `4` erreur |
| 7–8 | adresse du module et taille code/données/BSS |
| 9–10 | adresse de mailbox et taille `2196` |
| 11 | code d’erreur, `0` si prêt |

Python scanne MEM1 pour ce descripteur, vérifie version, région, état, alignement et bornes, puis vérifie `APSM` et la version `2` de la mailbox. Il refuse plusieurs candidats valides et revérifie les données avant écriture. L’état prêt est publié après l’initialisation, avec une barrière mémoire.

La mailbox utilise des entiers u32 big endian :

| Offset en octets | Contenu |
|---|---|
| `0`, `4`, `8` | `magic = 0x4150534D`, `version = 2`, `testValue = 0` |
| `12`, `16` | `writeCount`, `readCount`, initialement zéro |
| `20` | 32 événements de 68 octets : nom de galaxie sur 64 octets, puis ID u32 |

`EventQueue.hpp` publie chaque événement après une barrière mémoire. Les compteurs progressent modulo 2^32 ; le slot est le compteur modulo 32. Une file pleine refuse l’événement et affiche un log. Cette file volatile exige un seul producteur et un seul consommateur ; elle ne remplace pas une sauvegarde persistante des checks.

Les constantes Python sont dans `archipelago_protocol.py`. Toute évolution du format C++ exige de mettre à jour ces constantes, le contrôle de taille du build et la version attendue par le bootstrap.

Pour observer les événements d’une session déjà ouverte, sans les consommer :

```sh
.venv/bin/python test_mailbox.py --region RMGP01 --read-only \
  --shared-memory-pid <PID_AFFICHE> --observe-seconds 0 --watch
```

Pour lire et acquitter les événements via DME, lancer Dolphin comme enfant du script :

```sh
.venv/bin/python test_mailbox.py --region RMGP01 \
  --launch build/kamek/RMGP01/game/DATA/sys/main.dol \
  --observe-seconds 0 --watch --ack-events
```

Sans `--dolphin-user`, ce lancement utilise les paramètres habituels de Dolphin. L’argument reste disponible pour choisir un profil isolé. `--watch` affiche les changements jusqu’à Ctrl+C ; `--ack-events` avance `readCount` uniquement après lecture. Sans `--watch`, l’acquittement porte sur le lot lu une fois. `--read-only` et le backend de mémoire partagée refusent l’acquittement. Un seul client doit consommer la file. Le script ferme son instance enfant à la sortie, sauf avec `--keep-running`.

Codes d’erreur du bootstrap : `1` borne initiale incohérente ; `2` allocation hors arène ; `3` initialisation répétée ; `4` mauvaise région ; `5` fichier absent/ouverture impossible ; `6` mauvaise taille ; `7` lecture incomplète ; `8` empreinte incorrecte ; `9` mauvais header Kamek ; `10` mailbox/constructeur invalide ; `11` arène temporaire modifiée ; `12` instruction originale d’un hook incompatible ; `13` instruction installée incorrecte. Le bootstrap s’arrête sur erreur et le client affiche le code ; il ne poursuit pas avec un module partiellement valide.

## Diagnostic et distribution

```sh
./build.sh RMGP01 --diagnostic
python tools/test_archipelago.py
```

La cible de diagnostic conserve les sources Petari de la branche et produit `build/REGION/main.dol` et son `product.sel`. **Elle n’inclut pas les objets AP et ne doit pas être confondue avec le DOL Kamek retail patché.** Aucun déploiement automatique dans les dossiers de jeu existants n’est effectué.

L’ancien callback `archipelago_link_order`, la génération de `archipelago-mailbox.json` et les anciens scripts MAP/layout ont été supprimés. Le client découvre la mailbox en mémoire via le descripteur versionné. Le build Kamek contrôle lui-même les sections retail, le BSS, le point d’entrée et les deux hooks autorisés.

Sorties de la nouvelle cible sous `build/kamek/REGION/` :

- `REGION.bin` : module personnalisé ; `bootstrap.bin` : code/data/BSS du bootstrap.
- `manifest.json` : hashes, tailles, symboles, hooks et résultat du contrôle des sections.
- `REGION.xml` : patch Riivolution expérimental ; placer cet XML sous `riivolution/` et les deux `.bin` sous `Archipelago/REGION/` sur la carte SD.
- `main.dol` : **copie de test locale contenant le jeu**, à ne pas redistribuer.

Le package public doit contenir les modules, le bootstrap, les patches et leurs métadonnées, jamais le DOL retail ou les assets. La variante Riivolution est générée mais n’a pas été validée sur Wii ; les tests ci-dessous chargent la copie DOL patchée dans Dolphin.

## Résultats de validation

Environnement : Linux, Dolphin **2609-1.1**, DME **1.3.1**, toolchain Petari `GC/3.0a5`, Kamek épinglé ci-dessus. Les dimensions ci-dessous correspondent au module v2 avec `StarLog.cpp`, construit le 24 octobre 2026.

| Vérification | Résultat |
|---|---|
| Build du module et du bootstrap PAL/USA | Réussi |
| Taille module fichier / code-données / BSS | `3 740 / 3 560 / 8` octets |
| Allocation permanente du module arrondie | `3 584` octets |
| Bootstrap chargé | `4 384` octets |
| Chargement réel Dolphin PAL | Réussi ; `0x12345678` écrit et relu, stable 10 s |
| Chargement réel Dolphin USA | Réussi ; `0x12345678` écrit et relu, stable 10 s |
| Constructeur et initialisation BSS | Vérifiés avant état prêt, dans Dolphin |
| Bornes observées | Bootstrap `806BFFA0..806C10C0`, module `806C10C0..806C1EC0` alloué ; root heap commence après |
| Sections retail et point d’entrée/BSS | Deux mots de hooks autorisés ; reste préservé |
| SEL retail contre ELF retail exact | 0 export à modifier, pour les deux régions |
| Module corrompu dans une copie privée | Refusé dans Dolphin avec erreur `8`, puis fichier restauré |
| Hook StageResult dans Dolphin PAL/USA | Appel installé vers le module, vérifié par lecture mémoire |
| Instruction StageResult modifiée dans une copie négative PAL | Refusée dans Dolphin avec erreur `12` |
| Wrapper et bootstrap PowerPC dans Unicorn PAL/USA | Log et événement v2 uniques (`TestGalaxy`, `42`), appel original unique, retour et registres préservés ; refus d’un site incompatible avant installation |
| Tests unitaires | 46 réussis : protocole v2, file FIFO, débordement des compteurs, acquittement, bornes, lecture seule, formats, hooks et déploiement protégé |
| Diagnostic Petari PAL et USA, sans objets AP | Deux compilations réussies ; DOL identiques au retail après déplacement du log dans le module, aucun symbole AP dans les ELF |
| Copie privée après reconstruction | DOL et module identiques aux artefacts courants, pour les deux régions |
| Protection des fichiers sources | Empreintes DOL/SEL/boot des sources de jeu et DOL/SEL retail inchangées |
| Fixture historique 34 objets / 32 constructeurs-destructeurs / alignement 32 | Rejouée avec Kamek intégré : 4 chargements Unicorn réussis + 2 échecs d’allocation attendus ; alignement 64 rejeté |
| Protection Yama | Valeur `1`, inchangée |

Les adresses ci-dessus sont des **mesures de ce build**, jamais des constantes de découverte Python. Les essais de module corrompu et de stress à 34 objets ont eu lieu pendant l’investigation, avant l’ajout du hook StageResult. Le refus du site modifié dans Dolphin et les builds Petari de diagnostic ont été validés avant la mailbox v2 ; ces essais historiques ne sont pas présentés comme de nouveaux essais du module actuel. Les résultats v2 sont sous `build/kamek/` (`commit-v2-hooks-tests.json` et `REGION/commit-v2-dolphin-test.json`).

Le test PowerPC peut être reproduit après les deux builds :

```sh
.venv/bin/pip install unicorn==2.1.4
.venv/bin/python tools/test_archipelago_hooks.py
```

Il exécute le vrai bootstrap, le loader et le wrapper compilés. Les appels aux services DVD/SDK et aux fonctions du jeu sont remplacés par des adaptateurs de test : le nom et l’ID y sont synthétiques (`TestGalaxy`, `42`). Ce test ciblé exige le hook d’exemple `StageResult` et n’est pas une collecte d’étoile dans Dolphin. Une évolution de sa logique peut nécessiter d’adapter les services simulés.

## Fichiers intégrés et migration

- `build.sh` remplace la compilation/déploiement monolithique par les deux cibles décrites ci-dessus. Une copie privée préparée explicitement est ensuite mise à jour ; les dossiers de jeu existants ne sont plus des destinations implicites.
- `configure.py` retrouve le contenu Petari commité : retrait de la bibliothèque AP, du callback d’ordre de liaison, de la génération de métadonnées mailbox et du MAP AP implicite. La règle existante de mise à jour du `product.sel` pour le diagnostic est conservée.
- `tools/build_archipelago.py`, `loader/ArchipelagoBootstrap.cpp` et `vendor/Kamek/` apportent le build, le bootstrap SMG1 et les sources épinglées du linker/loader avec leur licence et le correctif d’alignement.
- `src/Game/Archipelago/Module.cpp` fournit uniquement le marqueur de vérification du BSS et d’un constructeur. `ArchipelagoMailbox.cpp`, `ArchipelagoMailbox.hpp` et `EventQueue.hpp` définissent la mailbox v2 et sa file d’événements.
- `include/Game/Archipelago/Hooks.hpp`, `config/Archipelago/hooks.json` et les en-têtes générés fournissent les hooks d’appel sans modification manuelle du bootstrap pour chaque mécanique.
- `test_mailbox.py` remplace la lecture d’une ancienne adresse MAP par la découverte du descripteur en mémoire. Il conserve les modes DME et lecture seule de la mémoire partagée Linux ; `--manifest` vérifie les appels installés. `tools/test_archipelago.py` vérifie les limites du protocole, des formats et du déploiement. `tools/test_archipelago_hooks.py` est le test PowerPC optionnel du bootstrap et de l’exemple StageResult.
- `README.md` et ce guide documentent les cibles ; `.gitignore` exclut les sorties générées et l’environnement Python local. Les patches intermédiaires et le prototype expérimental ont été retirés après intégration de Kamek.

Les mécaniques du jeu et les originaux ne font pas partie de cette migration. Le build Petari de diagnostic conserve son outil `tools/update_product_sel.py` pour actualiser les exports de son propre DOL recompilé.

Avant l’archivage du worktree, ses profils Dolphin et leurs sauvegardes ont été copiés et vérifiés sous `build/kamek/RMGP01/preserved-investigation-user/` pour PAL et `build/kamek/RMGE01/dolphin-user/` pour USA. Le profil PAL déjà présent dans le checkout principal n’a pas été écrasé. Le lancement normal depuis Dolphin continue d’utiliser le profil habituel.

## Vérifications manuelles restantes

Lancer sans `--null-video`, avec une observation longue ou `--keep-running`, puis vérifier l’écran titre, le chargement d’une sauvegarde, une galaxie, un retour à l’observatoire et le Home Menu. Collecter une étoile puis passer l’écran de résultat jusqu’à la transition : le hook doit produire exactement un log `[ARCHIPELAGO] Star collected: ..., ID: ...`, puis le jeu doit poursuivre sa transition normalement. Ce passage en partie reste à valider manuellement pour PAL et USA ; les tests Dolphin automatiques ont validé le démarrage et l’installation de l’appel. Activer les logs OSREPORT dans Dolphin pour lire ce message.

La mailbox doit rester prête et conserver `testValue`. La file doit recevoir un événement contenant la galaxie et l’ID ; le client peut ensuite l’acquitter. Cela ne transmet pas encore de check à un serveur Archipelago. Tester ensuite les resets et les états sauvegardés séparément. Ne pas réutiliser un savestate provenant d’un autre build.

Cette étape livre une mailbox et des hooks d’appel vérifiés dans les deux jeux. Elle ne certifie pas encore l’endurance en partie, les signatures des hooks de futures mécaniques ou le cycle de vie Wii/Riivolution.
