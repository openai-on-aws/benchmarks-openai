"""Shared bounds for serialized studies accepted by the cloud coordinator."""
def study_input_limit(environment):
    maximum = int(environment.get('EVAL_MAX_INPUT_BYTES', '10000000'))
    if not 1 <= maximum <= 268435456:
        raise ValueError('EVAL_MAX_INPUT_BYTES must be between 1 and 268435456')
    return maximum
