# Board: providers

> Note: the reference image `providers-reference.png` named below is an
> html-rendered stand-in of variant A (the agreed look of the Providers page),
> not a screenshot captured from the live dashboard, which is unreachable in this
> environment. Replace it with a real capture against the live page before the
> contract gates a review.

## Providers page
- Open: route /#board, click #tabprov, viewport 1280x800
- Layout: master-detail page rendered into the board frame by BoardProvidersView.open()
  (opened from the Providers tab of the dashboard shell, next to Guards); the master list
  on the left shows the providers with their key state, a "New provider" button and the
  "Import from the environment" rows; the detail on the right is the form of the selected
  provider with the "Connection", "API key" and "Models" cards, a "Delete provider" button
  in its header, a "Fetch models" button in the Models card header.
- Reference image: providers-reference.png
- C1: the page header shows the title "Providers" and a "Back to missions" button.
- C2: the master list shows one entry per provider with its name and a key-state label that reads "key set" or "no key", and "off" after a disabled provider's name.
- C3: the master list shows "No providers yet." when the database has no providers (API answer: GET /api/board/providers returns {"providers": []}).
- C4: the master list has a "New provider" button.
- C5: under the list the "Import from the environment" section shows a "Create" button per proposed row and "Nothing to import." when there are none (API answer: GET /api/board/providers/import-from-env returns {"proposals": []}).
- C6: the detail of a selected provider opens a card titled "Connection" with the fields "Name", "Label", "API type", "Base URL", "Enabled" and "Notes", a "Save" button and a "Close" button.
- C7: the detail has a card titled "API key" that prints the key state ("key set, since {when}" or "no key"), offers a "New key" input with a "Save key" button, and never echoes the key value.
- C8: the detail has a card titled "Models" with a "Fetch models" button; with no stored models it shows "No models yet.".
- C9: the detail header has a "Delete provider" button; deleting asks "Delete provider {name}? Its models and its stored key are removed. This cannot be undone." and the success notice reads "Provider {name} deleted.".
- C10: clicking "Fetch models" opens a panel with a "Filter by name" input, a "Free only" checkbox, a "Select shown" button and an "Add selected ({n})" button.
- C11: no key value is visible anywhere on the page: the hint reads "Keys are never shown: only whether a key is set and when it was set.".
