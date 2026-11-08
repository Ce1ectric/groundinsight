from groundinsight.models.core_models import ComplexNumber, json_safe_float


def serialize_impedance(impedance_dict):
    """
    Serialize an impedance dictionary into a JSON-compatible format.

    This function converts a dictionary mapping frequencies to `ComplexNumber` instances
    into a dictionary where frequencies are strings and impedance values are dictionaries
    containing 'real' and 'imag' components.

    Parameters
    ----------
    impedance_dict : Dict[float, ComplexNumber]
        A dictionary mapping each frequency to its
        corresponding `ComplexNumber` impedance.

    Returns
    -------
    Dict[str, Dict[str, float]]
        A JSON-compatible dictionary where each key is a frequency
        represented as a string, and each value is a dictionary
        with 'real' and 'imag' keys corresponding to the impedance components.

    Notes
    -----
    Non-finite components are written as the strings ``"inf"``, ``"-inf"``
    and ``"nan"``. The open-end sentinel of this package *is* an infinite
    impedance, and ``json.dumps`` would otherwise emit the bare
    ``Infinity`` literal, which SQLite's ``json_valid()`` rejects and no
    non-Python reader accepts. :func:`deserialize_impedance` reads the
    strings back through the ``ComplexNumber`` validator, so the round-trip
    is lossless.
    """
    return {
        str(freq): {
            "real": json_safe_float(imp.real),
            "imag": json_safe_float(imp.imag),
        }
        for freq, imp in impedance_dict.items()
    }


def deserialize_impedance(impedance_json):
    """
    Deserialize a JSON-compatible impedance dictionary back into its original format.

    This function converts a dictionary where frequencies are strings and impedance values are
    dictionaries containing 'real' and 'imag' components into a dictionary mapping frequencies
    as floats to `ComplexNumber` instances.

    Parameters
    ----------
    impedance_json : Dict[str, Dict[str, float]]
        A JSON-compatible dictionary where each key is
        a frequency represented as a string, and each value
        is a dictionary with 'real' and 'imag' keys.

    Returns
    -------
    Dict[float, ComplexNumber]
        A dictionary mapping each frequency to its corresponding
        `ComplexNumber` impedance.

    Raises
    ------
    ValueError
        If the input JSON does not conform to the expected format.
    """
    try:
        return {
            float(freq): ComplexNumber(**imp) for freq, imp in impedance_json.items()
        }
    except (TypeError, ValueError) as e:
        raise ValueError(f"Error deserializing impedance: {e}") from e
