import discord
from discord.ext import commands


class TicketView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)  # Persistent view across bot restarts
        self.bot = bot

    @discord.ui.button(
        label="Create Ticket", 
        style=discord.ButtonStyle.primary, 
        custom_id="create_ticket_btn",
        emoji="🎫"
    )
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Ticket creation logic here
        await interaction.response.send_message(
            "🎫 Ticket creation initiated! A staff member will be with you shortly.", 
            ephemeral=True
        )


class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def get_ticket_view(self):
        return TicketView(self.bot)

    async def deploy_panel_from_dashboard(
        self, 
        channel_id: int, 
        title: str, 
        description: str, 
        category_id: int = None, 
        support_role_id: int = None,
        color: str = "#58b9ff",
        image_url: str = None,
        thumbnail_url: str = None,
        footer_text: str = None,
        fields: list = None
    ):
        """
        Callable method invoked directly by Flask's /dashboard/tickets route.
        """
        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return False, f"Channel with ID {channel_id} could not be found."

        # 1. Parse Hex Accent Color
        try:
            hex_val = color.lstrip("#")
            embed_color = discord.Color(int(hex_val, 16))
        except Exception:
            embed_color = discord.Color.blue()

        # 2. Construct Embed Base
        embed = discord.Embed(
            title=title,
            description=description,
            color=embed_color
        )

        # 3. Add Optional Custom Media & Footer
        if image_url:
            embed.set_image(url=image_url)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        if footer_text:
            embed.set_footer(text=footer_text)

        # 4. Process Dynamic JSON Fields
        if fields and isinstance(fields, list):
            for f in fields:
                field_name = f.get("name", "").strip()
                field_value = f.get("value", "").strip()
                is_inline = bool(f.get("inline", False))

                if field_name and field_value:
                    embed.add_field(
                        name=field_name,
                        value=field_value,
                        inline=is_inline
                    )

        # 5. Send Panel to Discord
        try:
            await channel.send(embed=embed, view=self.get_ticket_view())
            return True, "Panel deployed successfully!"
        except discord.Forbidden:
            return False, "Bot lacks permissions to send messages in that channel."
        except Exception as e:
            return False, f"Failed to send embed: {str(e)}"


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
