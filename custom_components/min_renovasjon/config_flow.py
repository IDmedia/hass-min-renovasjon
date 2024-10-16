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

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        address = None

        if user_input is not None:
            try:
                address = user_input["address"]
                error, address_info = await self._get_address_info(address)

                if error is not None:
                    errors["base"] = error
                else:
                    # Test if the address is supported by making a garbage collection data request
                    error, is_supported = await self._test_address_support(address_info)
                    if not is_supported:
                        errors["base"] = error
                    else:
                        return self.async_create_entry(title="Min Renovasjon", data=address_info)

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

    async def _get_address_info(self, address_search_string):
        """Get address information based on user input."""
        error, address_info = await self._address_lookup(address_search_string)

        if error is not None:
            return error, None

        if address_info is not None:
            return None, {
                const.CONF_STREET_NAME: address_info[0],
                const.CONF_STREET_CODE: str(address_info[1]),
                const.CONF_HOUSE_NUMBER: str(address_info[2]),
                const.CONF_MUNICIPALITY_NUMBER: str(address_info[4])
                const.CONF_APP_KEY: const.DEFAULT_APP_KEY
            }

        return "no_address_found", None

    async def _address_lookup(self, search_string: str):
        """Make an API call to get address details."""
        regex = r"(.*ve)(i|g)(.*)"
        subst = "\\1*\\3"
        search_string = re.sub(regex, subst, search_string, 0, re.MULTILINE)

        params = {
            "sok": search_string,
            # Specify fields to return, modify as necessary
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

                    if not data["adresser"]:
                        return "no_address_found", None

                    if len(data["adresser"]) > 1:
                        return "multiple_addresses_found", None

                    # Extract address details from the API response
                    return None, (
                        data["adresser"][0]["adressenavn"],  # Street name
                        data["adresser"][0]["adressekode"],   # Street code
                        data["adresser"][0]["nummer"],        # House number
                        data["adresser"][0]["kommunenavn"],   # Municipality name
                        data["adresser"][0]["kommunenummer"],  # Municipality number
                        data["adresser"][0]["postnummer"],     # Postal code
                        data["adresser"][0]["poststed"],       # Postal locality
                    )

        return "municipality_not_customer", None

    async def _test_address_support(self, address_info):
        """Make a test request to check if the address is supported."""
        kommunenr = address_info[const.CONF_MUNICIPALITY_NUMBER]
        gatenavn = address_info[const.CONF_STREET_NAME]
        gatekode = address_info[const.CONF_STREET_CODE]
        husnr = address_info[const.CONF_HOUSE_NUMBER]
        app_key = const.DEFAULT_APP_KEY

        # Construct the target URL for the garbage collection API
        target_url = (
            f"https://komteksky.norkart.no/MinRenovasjon.Api/api/tommekalender/"
            f"?kommunenr={kommunenr}&gatenavn={gatenavn}&gatekode={gatekode}&husnr={husnr}"
        )

        # Construct the URL for the proxy server
        url = f"{const.PROXY_SERVER_URL}{target_url}"

        headers = {
            "RenovasjonAppKey": app_key,
            "Kommunenr": kommunenr
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    # Get the response content as text
                    response_content = await response.text()  
                    
                    if response.status != 200:
                        return "municipality_not_customer", False

                    # Try to decode as JSON regardless of content type
                    try:
                        calendar_data = await response.json()
                    except aiohttp.ContentTypeError:
                        calendar_data = json.loads(response_content)

                    # If the result is an empty list
                    if not calendar_data:
                        return "municipality_not_customer", False

                    return None, True

        except Exception as err:
            _LOGGER.error(f"Error during test request: {err}")
            return "unknown", False
