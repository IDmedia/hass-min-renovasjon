import re
import json
import logging
import aiohttp
from . import const
import voluptuous as vol
from homeassistant import config_entries

_LOGGER = logging.getLogger(__name__)

class GarbageCalendarConfigFlow(config_entries.ConfigFlow, domain=const.DOMAIN):
    """Handle a config flow for Garbage Collection Calendar."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self):
        self._candidate_addresses = []

    async def async_step_user(self, user_input=None):
        errors = {}
        address = None

        if user_input is not None:
            try:
                address = user_input["address"]
                error, addresses = await self._address_lookup(address)

                if error is not None:
                    errors["base"] = error
                else:
                    self._candidate_addresses = addresses
                    return await self.async_step_select_address()

            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("address", default=address): str
            }),
            errors=errors
        )

    async def async_step_select_address(self, user_input=None):
        errors = {}

        if user_input is not None:
            try:
                new_search = (user_input.get("new_search") or "").strip()
                if new_search:
                    error, addresses = await self._address_lookup(new_search)
                    if error is not None:
                        errors["base"] = error
                    else:
                        self._candidate_addresses = addresses
                        return await self.async_step_select_address()
                elif user_input.get("address") is not None:
                    idx = int(user_input["address"])
                    address_info = self._build_address_info(self._candidate_addresses[idx])
                    error, is_supported = await self._test_address_support(address_info)
                    if not is_supported:
                        errors["base"] = error
                    else:
                        return self.async_create_entry(title="Min Renovasjon", data=address_info)
                else:
                    errors["base"] = "select_or_search"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        options = {
            str(i): f"{a['adressenavn']} {a['nummer']}, {a['postnummer']} {a['poststed']}"
            for i, a in enumerate(self._candidate_addresses)
        }
        return self.async_show_form(
            step_id="select_address",
            data_schema=vol.Schema({
                vol.Optional("address"): vol.In(options),
                vol.Optional("new_search"): str,
            }),
            errors=errors,
        )

    def _build_address_info(self, addr):
        return {
            const.CONF_STREET_NAME: addr["adressenavn"],
            const.CONF_STREET_CODE: str(addr["adressekode"]),
            const.CONF_HOUSE_NUMBER: str(addr["nummer"]),
            const.CONF_MUNICIPALITY_NUMBER: str(addr["kommunenummer"]),
            const.CONF_APP_KEY: const.DEFAULT_APP_KEY,
        }

    async def _address_lookup(self, search_string: str):
        regex = r"(.*ve)(i|g)(.*)"
        subst = "\\1*\\3"
        search_string = re.sub(regex, subst, search_string, 0, re.MULTILINE)

        params = {
            "sok": search_string,
            "treffPerSide": 10,
            "filtrer": "adresser.kommunenummer,"
                       "adresser.adressenavn,"
                       "adresser.adressekode,"
                       "adresser.nummer,"
                       "adresser.kommunenavn,"
                       "adresser.postnummer,"
                       "adresser.poststed",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url=const.ADDRESS_LOOKUP_URL, params=params) as resp:
                response = await resp.read()
                if resp.ok:
                    data = json.loads(response.decode("UTF-8"))
                    addresses = data["adresser"]

                    if not addresses:
                        return "no_address_found", None

                    if data.get("metadata", {}).get("totaltAntallTreff", len(addresses)) > 10:
                        return "too_many_addresses", None

                    return None, addresses

        return "municipality_not_customer", None

    async def _test_address_support(self, address_info):
        municipality_number = address_info[const.CONF_MUNICIPALITY_NUMBER]
        street_name = address_info[const.CONF_STREET_NAME]
        street_code = address_info[const.CONF_STREET_CODE]
        house_number = address_info[const.CONF_HOUSE_NUMBER]
        app_key = const.DEFAULT_APP_KEY

        target_url = (
            f"https://komteksky.norkart.no/MinRenovasjon.Api/api/tommekalender/"
            f"?kommunenr={municipality_number}&gatenavn={street_name}&gatekode={street_code}&husnr={house_number}"
        )
        url = f"{const.PROXY_SERVER_URL}{target_url}"
        headers = {
            "RenovasjonAppKey": app_key,
            "Kommunenr": municipality_number
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    response_content = await response.text()

                    if response.status != 200:
                        return "municipality_not_customer", False

                    try:
                        calendar_data = await response.json()
                    except aiohttp.ContentTypeError:
                        calendar_data = json.loads(response_content)

                    if not calendar_data:
                        return "municipality_not_customer", False

                    return None, True

        except Exception as err:
            _LOGGER.error(f"Error during test request: {err}")
            return "unknown", False
