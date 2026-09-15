# Site

A minimal static web page. Open `index.html` in a browser to view it; no build step or server is needed.

## Files

### index.html

The page markup. It sets the character encoding, viewport and title, links `style.css`, and lays out the page in three parts:

- A header with the site title and navigation links to the page sections.
- A main area with an About section and a Contact section containing placeholder text.
- A footer with a copyright line.

### style.css

The page styles. It uses border-box sizing everywhere, a system font stack with comfortable line height, and a light background. The header, main content and footer are centered and capped at 48rem wide. The header is a flex row that wraps on narrow screens, navigation links are blue and underline on hover, sections are spaced apart, and the footer text is smaller and gray.
