# Category photography

Original supplied AI category illustrations: pizzas.png, hamburguesas.png,
alitas.png, papitas.png. These represent categories, not exact recipes or variants.

The frontend uses local 400px and 800px JPEG derivatives (quality 82) through
srcset and sizes. Originals remain unchanged. Regenerate both sizes when replacing
an original; keep the square framing and center the subject for the 6:5 card crop.
No image-processing runtime dependency is required.

category-card.js maps API category enums to these asset stems. Images are decorative;
the adjacent category name labels each link. Failed images become a cream/green
text panel without extra network requests. Product rows intentionally have no photo.
