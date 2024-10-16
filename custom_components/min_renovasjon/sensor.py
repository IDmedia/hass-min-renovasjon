import json
import logging
import aiohttp
from . import const
from datetime import datetime, timedelta
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import CONF_NAME

_LOGGER = logging.getLogger(__name__)

# Define how often to update (e.g., every hour)
UPDATE_INTERVAL = timedelta(hours=1)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Garbage Collection sensors using config entry."""
    coordinator = GarbageCollectionCoordinator(hass, entry)

    # Fetch initial data
    await coordinator.async_refresh()

    # Check if we successfully fetched data before adding the entity
    if coordinator.last_update_success:
        async_add_entities(coordinator.sensors)

class GarbageCollectionCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch garbage collection data."""

    def __init__(self, hass, entry):
        """Initialize the Garbage Collection coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Garbage Collection Calendar",
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = entry
        self.sensors = []

    async def _async_update_data(self):
        """Fetch data from the external garbage collection service."""
        # Extract user input from the entry
        kommunenr = self.entry.data.get(const.CONF_MUNICIPALITY_NUMBER)
        app_key = self.entry.data.get(const.CONF_APP_KEY)
        gatenavn = self.entry.data.get(const.CONF_STREET_NAME)
        gatekode = self.entry.data.get(const.CONF_STREET_CODE)
        husnr = self.entry.data.get(const.CONF_HOUSE_NUMBER)

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

        # Log the request details for debugging
        # _LOGGER.debug(f"Making request to URL: {url}")
        # _LOGGER.warning(f"Request headers: {headers}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    # Get the response content as text
                    response_content = await response.text()  

                    # Check if response status is 200
                    if response.status != 200:
                        raise UpdateFailed(f"Error fetching data: {response.status}, content: {response_content}")

                    # Try to decode as JSON regardless of content type
                    try:
                        calendar_data = await response.json()
                    except aiohttp.ContentTypeError:
                        calendar_data = json.loads(response_content)

                    # Now fetch the fractions data
                    fractions_url = f"{const.PROXY_SERVER_URL}https://komteksky.norkart.no/MinRenovasjon.Api/api/fraksjoner/"
                    async with session.get(fractions_url, headers=headers) as fractions_response:
                        fractions_content = await fractions_response.text()

                        if fractions_response.status != 200:
                            raise UpdateFailed(f"Error fetching fractions data: {fractions_response.status}, content: {fractions_content}")

                        # Parse fractions data from HTML response
                        try:
                            fractions_data = await fractions_response.json()
                        except aiohttp.ContentTypeError:
                            fractions_data = json.loads(fractions_content)

                    # Merge the two datasets on the FraksjonId
                    self.sensors.clear()
                    for item in calendar_data:
                        fraksjon_id = item.get("FraksjonId")
                        # Find the matching fraction data
                        fraction = next((f for f in fractions_data if f["Id"] == fraksjon_id), None)
                        if fraction:
                            # Pass the Tommedatoer directly into the sensor
                            sensor = GarbageCollectionSensor(self, item, fraction, item.get("Tommedatoer", []))
                            self.sensors.append(sensor)

                    return calendar_data  # You may return calendar_data if needed

        except Exception as err:
            raise UpdateFailed(f"Error fetching garbage collection data: {err}")

class GarbageCollectionSensor(SensorEntity):
    """Sensor to represent the garbage collection schedule."""

    def __init__(self, coordinator, calendar_item, fraction, tommedatoer):
        """Initialize the sensor."""
        self.coordinator = coordinator
        self.calendar_item = calendar_item
        self.fraction = fraction
        self.tommedatoer = tommedatoer
        self._attr_name = fraction.get("Navn", "Unknown Bin")
        self._attr_unique_id = f"bin_fraction_{fraction['Id']}"
        self._attr_state = self.calculate_days_until_next_collection()
        self._attr_icon = "mdi:trash-can"

    @property
    def state(self):
        """Return the state of the sensor, which is the days until the next collection."""
        return self._attr_state

    def calculate_days_until_next_collection(self):
        """Calculate the number of days until the next collection date."""
        if self.tommedatoer:
            # Parse the first date from the Tommedatoer list
            next_date = datetime.strptime(self .tommedatoer[0], "%Y-%m-%dT%H:%M:%S")
            
            # Calculate the time difference
            time_difference = next_date - datetime.now()
            
            # Calculate total days including fractional days
            total_days = time_difference.total_seconds() / (24 * 60 * 60)
            
            # If more than 1 day but less than 2, it should still count as 1 day remaining
            return max(0, round(total_days))
        
        return "Unknown" 

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the sensor."""
        attributes = {
            "FractionId": self.calendar_item.get("FraksjonId"),
            "Name": self.fraction.get("Navn"),
            "CollectionDates": self.tommedatoer,
            "FractionIcon": self.fraction.get("NorkartStandardFraksjonIkon"),
        }
        return attributes

    async def async_update(self):
        """Update the sensor using the coordinator."""
        await self.coordinator.async_request_refresh()
        # Recalculate the state whenever the sensor is updated
        self._attr_state = self.calculate_days_until_next_collection()
