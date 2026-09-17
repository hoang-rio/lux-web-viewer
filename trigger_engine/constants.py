"""Constants shared across the trigger engine."""

VALID_FIELDS = {"soc", "p_pv", "p_discharge", "p_charge", "fac", "p_eps", "p_to_grid", "p_to_user", "v_bat"}
VALID_OPERATORS = {">", "<", ">=", "<=", "==", "!="}
VALID_ACTION_TYPES = {"tuya_on", "tuya_off", "tuya_toggle", "tuya_set", "notification", "play_audio"}