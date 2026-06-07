"""Dynamic credentials input form component."""

import streamlit as st

from config.credentials import save_credentials
from config.settings import CREDENTIALS_REQUIRED


def render_credentials_form(source_name: str) -> None:
    """Render a credential input form appropriate for the selected data source.

    Shows a "No credentials needed" notice for Yahoo Finance and NSE India.
    For broker sources renders text inputs and a Save button.

    Args:
        source_name: Currently selected data source name.
    """
    required_fields = CREDENTIALS_REQUIRED.get(source_name, [])

    if not required_fields:
        st.success(f"{source_name}: No credentials needed")
        return

    st.markdown(f"**{source_name} Credentials**")

    saved = st.session_state.get("credentials", {}).get(source_name, {})
    field_values: dict[str, str] = {}

    for field in required_fields:
        label = field.replace("_", " ").title()
        input_type = "password" if field in {"api_secret", "access_token", "password"} else "default"
        field_values[field] = st.text_input(
            label,
            value=saved.get(field, ""),
            type=input_type,
            key=f"cred_{source_name}_{field}",
        )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Save Credentials", key=f"save_creds_{source_name}"):
            if all(field_values.values()):
                try:
                    save_credentials(source_name, field_values)
                    creds = st.session_state.get("credentials", {})
                    creds[source_name] = field_values
                    st.session_state.credentials = creds
                    st.success("Credentials saved!")
                except Exception as exc:
                    st.error(f"Failed to save: {exc}")
            else:
                st.warning("Please fill in all fields.")
    with col2:
        if saved and all(saved.get(f) for f in required_fields):
            st.success("Connected")
        else:
            st.warning("Not configured")
