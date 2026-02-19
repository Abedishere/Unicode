# Task

at the moment, when i upload a picture in the ai-orchestrator unicode, i get the path in "", however this is incoient, so i made a /image command which if put before the path and then press enter it puts the image in a top menue above the text however this is very inconvenient and i want it to be done automatically so whenever i upload a picture onto the terminal through drag drop i want it to automatically be put in that top menue.

# Implementation Plan

Now I have a clear picture of the codebase. Let me assess the plan.

**APPROVED**

The plan is solid. Key observations from my review:

1. **Existing primitives are correct** — `_clean_path`, `_is_image_path`, and `_try_attach_image` all exist and work as described. Reusing them is the right call.

2. **Insertion points are accurate** — Line 894 already handles single-line image detection via `_try_attach_image(stripped)`, but when a drag-drop produces a multi-line paste (e.g., `& 'C:\path\to\image.png'` with trailing newlines), control flows past that check into the `if paste:` block at line 899 and gets attached as text instead. The plan correctly identifies both the primary paste branch (line 899) and the continuation paste branch (line 941) as the places to intercept.

3. **The `_paste_is_image_path` helper makes sense** — It bridges the gap between multi-line paste payloads and the existing single-string `_is_image_path`. Collapsing non-blank lines and testing the full payload is the right heuristic for drag-drop artifacts.

4. **Ordering is correct** — The new check must go *before* the text-attachment logic in both branches, which the plan specifies.

5. **Scope is appropriately limited** — `/image` command untouched, no unnecessary refactoring.

One minor note for the developer: in step 3, the `_erase_screen_from(saved=True)` call and `continue` should mirror the pattern already at lines 894–896. In step 4 (continuation branch), do **not** call `_erase_screen_from` — the existing continuation image handler at line 937–938 doesn't call it either; just `continue` to stay in the multiline loop.
