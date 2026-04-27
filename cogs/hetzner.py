import discord
from discord.app_commands import (
    Choice,
    Range,
    command,
    describe,
    guild_only,
)
from discord.app_commands.checks import bot_has_permissions, cooldown
from discord.ext import commands, tasks
from discord.interactions import Interaction
from discord.ui import Button
from settings import settings
from typing import Literal, Optional
from bson import ObjectId
from bson.errors import InvalidId
import asyncio
import datetime


class Hetzner(commands.Cog, name="hetzner"):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.check_auction.start()

    @tasks.loop(minutes=31, reconnect=True)
    async def check_auction(self):
        print("Checking Hetzner auction...")

        # Fetch all configs from the database
        configs = await self.bot.db.hetzner.find().to_list(length=None)
        if not configs:
            print("No Hetzner configs found.")
            return
        print(f"Found {len(configs)} Hetzner configs.")

        # Get the channels to send notifications
        channel = self.bot.get_channel(
            settings.hetzner_notifications_channel_id
        ) or await self.bot.fetch_channel(settings.hetzner_notifications_channel_id)
        if not channel:
            print("Hetzner auction notifications channel not found.")
            return

        # Fetch the data from Hetzner
        usd_url = (
            "https://www.hetzner.com/_resources/app/data/app/live_data_sb_USD.json"
        )
        eur_url = (
            "https://www.hetzner.com/_resources/app/data/app/live_data_sb_EUR.json"
        )
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15"
        }

        usd_request = await self.bot.session.get(usd_url, headers=headers)
        await asyncio.sleep(5)  # Just to be nice to Hetzner
        eur_request = await self.bot.session.get(eur_url, headers=headers)

        if usd_request.status != 200 or eur_request.status != 200:
            print(usd_request.status, await usd_request.text())
            print(eur_request.status, await eur_request.text())
            return

        usd_data = await usd_request.json()
        eur_data = await eur_request.json()

        print("Fetched data from Hetzner.")

        configs_to_delete = []

        # Sift through the configs to find matching servers
        for config in configs:
            currency = config.get("currency", "EUR")

            price = config.get("price", 0)
            vat_percentage = config.get("vat_percentage", 0)

            location = config.get("location", None)

            cpu = config.get("cpu", None)

            ram_size = config.get("ram_size", None)
            ram_ecc = config.get("ram_ecc", None)

            hdd_size = config.get("hdd_size", None)
            hdd_count = config.get("hdd_count", None)
            hdd_type = config.get("hdd_type", None)

            user_id = config.get("user_id", 0)

            data = eur_data
            if currency == "USD":
                data = usd_data

            # compute price_excl only if price_limit is set
            price_excl = price * (1 - vat_percentage / 100)

            # Filter the data based on the user's requirements
            filtered_data = []
            for server in data.get("server", []):
                p = server.get("price", 0)
                dd = server.get("datacenter", "")
                sd = server.get("serverDiskData", {})

                if price_excl and p > price_excl:
                    continue
                if location and location not in dd:
                    continue
                if cpu and cpu not in server.get("cpu", ""):
                    continue
                if ram_size is not None and server.get("ram_size", 0) < ram_size:
                    continue
                if ram_ecc is not None and server.get("is_ecc", False) not in [
                    ram_ecc,
                    True,
                ]:
                    continue
                if hdd_size is not None and server.get("hdd_size", 0) < hdd_size:
                    continue
                if hdd_count is not None and server.get("hdd_count", 0) < hdd_count:
                    continue
                if hdd_type:
                    if not sd.get(hdd_type):
                        continue

                filtered_data.append(server)

            if not filtered_data:
                print(f"No matching servers for user {user_id}.")
                continue
            print(f"Found {len(filtered_data)} matching servers for user {user_id}.")

            # Data for the notification
            filtered_data = filtered_data[0]

            found_url = "https://www.hetzner.com/sb#search=" + str(filtered_data["id"])

            found_location = filtered_data.get("datacenter", "Unknown")
            found_cpu = filtered_data.get("cpu", "Unknown")
            found_ram_size = filtered_data.get("ram_size", 0)
            found_ram_ecc = "ECC" if filtered_data.get("is_ecc", False) else "Non-ECC"
            found_price = filtered_data.get("price", 0) * (1 + vat_percentage / 100)
            found_hdd_size = filtered_data.get("hdd_size", 0)
            found_hdd_count = filtered_data.get("hdd_count", 0)
            found_description = filtered_data.get("description", "Unknown")

            # Format the description
            long_items = [item for item in found_description if len(item) > 10]
            short_items = [item for item in found_description if len(item) <= 10]
            formatted_description = ""
            if long_items:
                formatted_description += "\n".join(long_items) + "\n"
            if short_items:
                formatted_description += " - ".join(short_items)

            # Send notification in the channel
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if user:
                embed = discord.Embed(
                    title="Server Found!",
                    description=f"### A server matching your requirements has been found in the Hetzner server auction.\n{formatted_description}",
                    color=0x00FF00,
                )
                embed.add_field(
                    name="Price",
                    value=f"{found_price} {currency} (incl. {vat_percentage}% VAT)",
                )
                embed.add_field(name="Location", value=found_location)
                embed.add_field(name="CPU", value=found_cpu)
                embed.add_field(
                    name="RAM", value=f"{found_ram_size} GB ({found_ram_ecc})"
                )
                embed.add_field(
                    name="Storage", value=f"{found_hdd_size} GB ({found_hdd_count} drives)"
                )

                view = discord.ui.View()
                view.add_item(
                    Button(
                        label="View Server",
                        url=found_url,
                        style=discord.ButtonStyle.link,
                    )
                )

                await channel.send(content=user.mention, embed=embed, view=view)

            configs_to_delete.append(config.get("_id"))

        print(f"Found {len(configs_to_delete)} matching servers.")

        # Delete configs that have been notified
        if configs_to_delete:
            await self.bot.db.hetzner.delete_many({"_id": {"$in": configs_to_delete}})
            print(f"Deleted {len(configs_to_delete)} configs from the database.")

        # Delete configs older than 90 days
        current_timestamp = int(
            datetime.datetime.now(tz=datetime.timezone.utc).timestamp()
        )
        await self.bot.db.hetzner.delete_many(
            {"timestamp": {"$lt": current_timestamp - 60 * 60 * 24 * 90}}
        )
        print("Deleted configs older than 90 days.")

    @command(
        name="hetzner",
        description="Get notified when a server from Hetzner server auction reaches your set requirements.",
    )
    @describe(
        price="The maximum price, including VAT",
        vat_percentage="The percentage of VAT you have to pay",
        currency="EUR or USD",
        location="The location of the server",
        cpu="AMD or Intel",
        ram_size="The minimum amount RAM, in GB",
        ram_ecc="Whether the RAM has to be ECC or not",
        drive_size="The minimum size of the largest drive, in GB",
        drive_count="The minimum amount of drives",
        drive_type="Server needs at least one specific drive type (NVMe, SATA SSD, or HDD)",
    )
    @guild_only()
    @bot_has_permissions(
        send_messages=True, embed_links=True, external_emojis=True, attach_files=True
    )
    @cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
    async def slash_hetzner(
        self,
        interaction: Interaction,
        price: Range[int, 0, 500] = 0,
        vat_percentage: Range[int, 0, 100] = 0,
        currency: Literal["EUR", "USD"] = "EUR",
        location: Literal["All Datacenters", "NBG", "FSN", "HEL"] = "All Datacenters",
        cpu: Literal["Any", "AMD", "Intel"] = "Any",
        ram_size: Range[int, 32, 1024] = 32,
        ram_ecc: bool = False,
        drive_size: Range[int, 256, 22528] = 256,
        drive_count: Range[int, 1, 16] = 1,
        drive_type: Literal["Any", "NVMe", "SATA", "HDD"] = "Any",
    ):
        """Get notified when a server from Hetzner server auction reaches your set requirements."""
        assert interaction.guild is not None
        assert interaction.user is not None
        assert interaction.channel is not None

        # Users can't have more than 10 configs
        user_configs = await self.bot.db.hetzner.count_documents(
            {"user_id": interaction.user.id}
        )
        if user_configs >= 10:
            return await interaction.response.send_message(
                ":x: | You can't have more than 10 configs!"
            )

        current_timestamp = int(
            datetime.datetime.now(tz=datetime.timezone.utc).timestamp()
        )

        # Make the config document
        data = {
            "user_id": interaction.user.id,
            "timestamp": current_timestamp,
            "currency": currency,
        }
        requirements = ""
        if price != 0:
            data["price"] = price
            requirements += f"> Less than {price} {currency}"
            if vat_percentage and vat_percentage != 0:
                data["vat_percentage"] = vat_percentage
                requirements += f" (incl. {vat_percentage}% VAT)"
            requirements += "\n"
        if location != "All Datacenters":
            data["location"] = location
            requirements += f"> Location: {location}\n"
        if cpu != "Any":
            data["cpu"] = cpu
            requirements += f"> CPU: {cpu}\n"
        if ram_size != 32:
            data["ram_size"] = ram_size
            requirements += f"> At least {ram_size}GB RAM\n"
        if ram_ecc:
            data["ram_ecc"] = ram_ecc
            requirements += "> RAM must be ECC\n"
        if drive_size != 256:
            data["hdd_size"] = drive_size
            requirements += f"> At least {drive_size}GB HDD\n"
        if drive_count != 1:
            data["hdd_count"] = drive_count
            requirements += f"> At least {drive_count} HDDs\n"
        if drive_type != "Any":
            data["hdd_type"] = drive_type.lower()
            requirements += f"> Has {drive_type} drives\n"
        await self.bot.db.hetzner.insert_one(data)

        # Send the confirmation message
        embed = discord.Embed(
            title="Requirements saved!",
            description=f"Your Hetzner server config has been saved and you will be notified when a server matching your requirements is found.\n\n{requirements}",
            color=0x00FF00,
        )
        embed.set_footer(
            text="You can save up to 10 server configs. Each server config is valid for 90 days.",
        )
        await interaction.response.send_message(embed=embed)

    def _config_label(self, config: dict) -> str:
        """Build a human-readable label for a config document."""
        parts = []
        currency = config.get("currency", "EUR")
        if price := config.get("price"):
            vat = config.get("vat_percentage", 0)
            parts.append(
                f"≤{price}{currency}" + (f" (incl. {vat}% VAT)" if vat else "")
            )
        if loc := config.get("location"):
            parts.append(loc)
        if cpu := config.get("cpu"):
            parts.append(cpu)
        ram = config.get("ram_size")
        ram_ecc = config.get("ram_ecc")
        if ram or ram_ecc:
            ram_str = f"{ram}GB" if ram else ""
            ecc_str = "ECC" if ram_ecc else ""
            parts.append(" ".join(filter(None, [ram_str, "RAM", ecc_str])))
        hdd_size = config.get("hdd_size")
        hdd_count = config.get("hdd_count")
        hdd_type = config.get("hdd_type")
        if hdd_size or hdd_count or hdd_type:
            size_str = f"{hdd_size}GB" if hdd_size else ""
            count_str = f"x{hdd_count}" if hdd_count else ""
            type_str = hdd_type.upper() if hdd_type else ""
            parts.append(" ".join(filter(None, [size_str, count_str, type_str])))
        if not parts:
            parts.append(f"Any {currency} server")
        return " | ".join(parts)[:100]

    @command(
        name="hetzner_remove",
        description="Remove a Hetzner server config.",
    )
    @guild_only()
    @describe(
        config="The config you want to remove. Leave empty to remove all configs."
    )
    @bot_has_permissions(
        send_messages=True, embed_links=True, external_emojis=True, attach_files=True
    )
    @cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
    async def slash_hetzner_remove(
        self, interaction: Interaction, config: Optional[str] = None
    ):
        """Remove your Hetzner server configs."""
        assert interaction.guild is not None
        assert interaction.user is not None
        assert interaction.channel is not None

        if config == "__all__" or config is None:
            await self.bot.db.hetzner.delete_many({"user_id": interaction.user.id})
            print(f"Removed all Hetzner configs for user {interaction.user.id}.")
            await interaction.response.send_message(
                "All your Hetzner server configs have been removed!"
            )
        else:
            try:
                oid = ObjectId(config)
            except InvalidId:
                print(f"Invalid ObjectId supplied for removal: {config!r} by user {interaction.user.id}")
                await interaction.response.send_message(
                    "Invalid config ID!", ephemeral=True
                )
                return

            deleted_doc = await self.bot.db.hetzner.find_one_and_delete(
                {"_id": oid, "user_id": interaction.user.id}
            )
            if deleted_doc is None:
                print(f"Config {config} not found or not owned by user {interaction.user.id}.")
                await interaction.response.send_message(
                    "Config not found!", ephemeral=True
                )
            else:
                print(f"Removed Hetzner config {config} for user {interaction.user.id}.")
                del_embed = discord.Embed(
                    title="Config Removed!",
                    description=f"The following config has been removed:\n{self._config_label(deleted_doc)}",
                    color=0xFF0000,
                )
                await interaction.response.send_message(embed=del_embed)

    @slash_hetzner_remove.autocomplete("config")
    async def hetzner_remove_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[Choice[str]]:
        query = current.strip().lower()
        configs = await self.bot.db.hetzner.find(
            {"user_id": interaction.user.id}
        ).to_list(length=None)
        choices = []

        if not query or query in {"all", "all configs", "__all__"}:
            choices.append(Choice(name="All configs", value="__all__"))

        for cfg in configs:
            label = self._config_label(cfg)
            value = str(cfg["_id"])
            if query and query not in label.lower() and query not in value.lower():
                continue
            choices.append(Choice(name=label, value=value))
            if len(choices) >= 11:
                break
        return choices

    @check_auction.before_loop
    async def before_remove_files(self):
        await self.bot.wait_until_ready()

    async def cog_unload(self):
        self.check_auction.cancel()


async def setup(bot):
    n = Hetzner(bot)
    await bot.add_cog(n)
