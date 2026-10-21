# Building a modified DOL

The retail `/ModuleData/product.sel` exports DOL symbols to
`HomeButtonMenuWrapperRSO.rso`. Its section-relative function offsets and absolute
small-data bases must match the linked executable. A matching vanilla DOL works
with the retail SEL; a modified DOL may require a new SEL even when section start
addresses do not change.

Configure a mod build with the retail SEL from the matching game's files:

```sh
python configure.py --version RMGE01 --non-matching \
  --product-sel orig/RMGE01/files/ModuleData/product.sel
ninja
```

The same C++ sources and generator support PAL (`RMGP01`). Select that version
and its own retail export table:

```sh
python configure.py --version RMGP01 --non-matching \
  --product-sel orig/RMGP01/files/ModuleData/product.sel
ninja
```

Outputs use the selected version directory (`build/RMGE01/` or `build/RMGP01/`).
Configure and build each version in turn: the generated `build.ninja` selects one
version at a time. Use each region's own original DOL and retail SEL; do not copy
the generated SEL from one region to the other. The generator reads each linked
ELF's symbol addresses, without a hardcoded regional delta.

Ninja uses the existing post-link build step to generate both:

- `build/RMGE01/main.dol`
- `build/RMGE01/files/ModuleData/product.sel`

Deploy both files together to a **copy** of the extracted game. Replace
`sys/main.dol` and `files/ModuleData/product.sel` in that copy. Keep the original
DOL, SEL and Dolphin user directory as references. Use a separate Dolphin user
directory (`dolphin-emu -u /absolute/path/to/test-user`) for tests.

`tools/update_product_sel.py` reads the ELF32 symbol table with Python's standard
library. It preserves the retail export names, hashes, SDK section IDs and table
layout, replacing only symbol values. It accepts defined global and weak symbols,
uses offsets relative to each ELF section and refreshes absolute exports such as
`_SDA_BASE_`. It rejects missing exports and incompatible sections. No DOL
instruction or retail input file is patched by this step.

Without `--product-sel`, the original build rules are unchanged. This option does
not bypass matching checks: use `--non-matching` for modified code. This workflow
retains the existing RSO interface; it does not make ABI changes to the external
module safe or introduce new RSO exports.

Run the small synthetic fixture tests with:

```sh
python tools/test_update_product_sel.py
```
