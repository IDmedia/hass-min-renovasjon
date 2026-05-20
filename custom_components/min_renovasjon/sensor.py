import json
import logging
import aiohttp
from . import const
from datetime import datetime, timedelta
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed, CoordinatorEntity

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(hours=1)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = GarbageCollectionCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    calendar_data, fractions_data = coordinator.data
    sensors = []
    for item in calendar_data:
        fraction_id = item.get("FraksjonId")
        fraction = next((f for f in fractions_data if f["Id"] == fraction_id), None)
        if fraction:
            sensors.append(GarbageCollectionSensor(coordinator, fraction_id, fraction))

    async_add_entities(sensors)


class GarbageCollectionCoordinator(DataUpdateCoordinator):

    def __init__(self, hass, entry):
        super().__init__(
            hass,
            _LOGGER,
            name="Garbage Collection Calendar",
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = entry

    async def _async_update_data(self):
        municipality_number = self.entry.data.get(const.CONF_MUNICIPALITY_NUMBER)
        app_key = self.entry.data.get(const.CONF_APP_KEY)
        street_name = self.entry.data.get(const.CONF_STREET_NAME)
        street_code = self.entry.data.get(const.CONF_STREET_CODE)
        house_number = self.entry.data.get(const.CONF_HOUSE_NUMBER)

        target_url = (
            f"https://komteksky.norkart.no/MinRenovasjon.Api/api/tommekalender/"
            f"?kommunenr={municipality_number}&gatenavn={street_name}&gatekode={street_code}&husnr={house_number}"
        )
        url = f"{const.PROXY_SERVER_URL}{target_url}"
        headers = {
            "RenovasjonAppKey": app_key,
            "Kommunenr": municipality_number,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    response_content = await response.text()
                    if response.status != 200:
                        raise UpdateFailed(
                            f"Error fetching data: {response.status}, content: {response_content}"
                        )
                    try:
                        calendar_data = await response.json()
                    except aiohttp.ContentTypeError:
                        calendar_data = json.loads(response_content)

                fractions_url = (
                    f"{const.PROXY_SERVER_URL}"
                    "https://komteksky.norkart.no/MinRenovasjon.Api/api/fraksjoner/"
                )
                async with session.get(fractions_url, headers=headers) as fractions_response:
                    fractions_content = await fractions_response.text()
                    if fractions_response.status != 200:
                        raise UpdateFailed(
                            f"Error fetching fractions data: {fractions_response.status}, content: {fractions_content}"
                        )
                    try:
                        fractions_data = await fractions_response.json()
                    except aiohttp.ContentTypeError:
                        fractions_data = json.loads(fractions_content)

            return calendar_data, fractions_data

        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error fetching garbage collection data: {err}")


class GarbageCollectionSensor(CoordinatorEntity, SensorEntity):

    def __init__(self, coordinator, fraction_id, fraction):
        super().__init__(coordinator)
        self.fraction_id = fraction_id
        self.fraction = fraction
        self._attr_name = fraction.get("Navn", "Unknown Bin")
        self._attr_unique_id = f"bin_fraction_{fraction['Id']}"
        self._attr_icon = "mdi:trash-can"

    def _get_collection_dates(self):
        if not self.coordinator.data:
            return []
        calendar_data, _ = self.coordinator.data
        item = next((i for i in calendar_data if i.get("FraksjonId") == self.fraction_id), None)
        return item.get("Tommedatoer", []) if item else []

    @property
    def state(self):
        collection_dates = self._get_collection_dates()
        if not collection_dates:
            return None
        today = datetime.now().date()
        future_dates = []
        for date_str in collection_dates:
            try:
                date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S").date()
                if date >= today:
                    future_dates.append(date)
            except ValueError:
                continue
        if future_dates:
            return (min(future_dates) - today).days
        return None

    @property
    def extra_state_attributes(self):
        return {
            "FractionId": self.fraction_id,
            "Name": self.fraction.get("Navn"),
            "CollectionDates": self._get_collection_dates(),
            "FractionIcon": self.fraction.get("NorkartStandardFraksjonIkon"),
        }
