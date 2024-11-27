from groundinsight.models.core_models import ComplexNumber


def serialize_impedance(impedance_dict):
    """
    Serialize an impedance dictionary into a JSON-compatible format.

    This function converts a dictionary mapping frequencies to `ComplexNumber` instances
    into a dictionary where frequencies are strings and impedance values are dictionaries
    containing 'real' and 'imag' components.

    Args:
        impedance_dict (Dict[float, ComplexNumber]): A dictionary mapping each frequency to its
                                                     corresponding `ComplexNumber` impedance.

    Returns:
        Dict[str, Dict[str, float]]: A JSON-compatible dictionary where each key is a frequency
                                     represented as a string, and each value is a dictionary
                                     with 'real' and 'imag' keys corresponding to the impedance components.
    """
    return {
        str(freq): {"real": imp.real, "imag": imp.imag}
        for freq, imp in impedance_dict.items()
    }


def deserialize_impedance(impedance_json):
    """
    Deserialize a JSON-compatible impedance dictionary back into its original format.

    This function converts a dictionary where frequencies are strings and impedance values are
    dictionaries containing 'real' and 'imag' components into a dictionary mapping frequencies
    as floats to `ComplexNumber` instances.

    Args:
        impedance_json (Dict[str, Dict[str, float]]): A JSON-compatible dictionary where each key is
                                                      a frequency represented as a string, and each value
                                                      is a dictionary with 'real' and 'imag' keys.

    Returns:
        Dict[float, ComplexNumber]: A dictionary mapping each frequency to its corresponding
                                    `ComplexNumber` impedance.

    Raises:
        ValueError: If the input JSON does not conform to the expected format.
    """
    try:
        return {
            float(freq): ComplexNumber(**imp) for freq, imp in impedance_json.items()
        }
    except (TypeError, ValueError) as e:
        raise ValueError(f"Error deserializing impedance: {e}")
