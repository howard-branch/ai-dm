from ai_dm.game.location_service import LocationService


class LocationManager:
    """Thin façade kept for backward compatibility."""

    def __init__(self, service: LocationService | None = None) -> None:
        self.service = service or LocationService()

    def current_location(self) -> str:
        return "candlekeep_gate"
