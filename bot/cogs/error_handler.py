# cogs/error_handler.py
import discord
from discord import app_commands
from discord.ext import commands

class ErrorHandlerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Register global tree error handler
        bot.tree.error(self.on_app_command_error)

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        # Catch missing permissions or general check failures
        if isinstance(error, (app_commands.MissingPermissions, app_commands.CheckFailure)):
            embed = discord.Embed(
                title="🚫 Access Denied",
                description="You do not have permission to use this command!",
                color=discord.Color.red()
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            print(f"❌ Command Exception: {error}")

async def setup(bot: commands.Bot):
    await bot.add_cog(ErrorHandlerCog(bot))
