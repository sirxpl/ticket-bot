"""Components V2 presentation for existing configurable embed content."""
import discord


def embed_layout(embed, *, content=None, view=None, text_budget=3500):
    view = view if view is not None else discord.ui.LayoutView(timeout=None)
    box = discord.ui.Container(accent_color=embed.colour or discord.Color.blurple())
    remaining = text_budget  # Callers may reserve space for surrounding components.
    def text(value, limit):
        nonlocal remaining
        value = str(value)
        if remaining <= 0:
            return
        limit = min(limit, remaining)
        value = value if len(value) <= limit else value[:max(0, limit - 1)] + "…"
        box.add_item(discord.ui.TextDisplay(value))
        remaining -= len(value)
    if content:
        text(content, 700)
    if embed.title:
        text("## " + embed.title, 250)
    if embed.description:
        text(embed.description, 1200)
    if embed.fields:
        box.add_item(discord.ui.Separator())
        for field in embed.fields[:12]:
            text(f"**{field.name[:200]}**\n{field.value}", 1100)
    if embed.image and embed.image.url:
        box.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(embed.image.url)))
    if embed.footer and embed.footer.text:
        text("-# " + embed.footer.text, 300)
    if embed.timestamp:
        text(f"-# <t:{int(embed.timestamp.timestamp())}:F>", 100)
    view.add_item(box)
    return view
