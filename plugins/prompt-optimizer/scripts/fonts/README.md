# Dashboard typography and colours

The dashboard combines OpenAI-inspired monochrome surfaces and restrained
typography with AWS orange (`#FF9900`) and Squid Ink (`#232F3E`). Orange is an
accent, with darker amber chart marks in day mode for contrast. Chart colours
identify model configurations, not providers.

The embedded font is **Open Sans**, distributed by AWS's
[Cloudscape design system](https://cloudscape.design/foundation/visual-foundation/typography/).
Regular (400) and bold (700) WOFF2 files were extracted unchanged from
`@cloudscape-design/global-styles` **1.0.72**, `fonts.css`. Their embedded
metadata identifies the **SIL Open Font License 1.1** and
`Copyright 2020 The Open Sans Project Authors (https://github.com/googlefonts/opensans)`.
See [LICENSE](LICENSE), obtained from
[Open Sans upstream](https://github.com/googlefonts/opensans/blob/main/OFL.txt),
and [NOTICE](NOTICE). The Cloudscape package's Apache notice is retained in
[CLOUDSCAPE-LICENSE](CLOUDSCAPE-LICENSE); its CSS header differs from the font
binaries' own licence metadata.

The renderer embeds both fonts and the licence notices into each HTML output,
so viewing a dashboard makes no font
requests to external services.

Package source:
https://registry.npmjs.org/@cloudscape-design/global-styles/-/global-styles-1.0.72.tgz

SHA-256:

- Regular: `cce577471c2586f3e0c2518fff84a970d33f61491fb8c629341b86f238cf07c0`
- Bold: `a61d67250a5c36640e22099937af31613e68d6134439d5d4329efea0372aea79`

OpenAI's custom **OpenAI Sans** is not redistributed here. Headings can use it
when it is already installed locally; otherwise they use the embedded Open Sans.
OpenAI brand reference: https://openai.com/brand/
