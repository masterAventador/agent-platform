use agent_platform_desktop::credentials::{validate_credential_key, CredentialError};

#[test]
fn accepts_stable_scoped_credential_keys() {
    assert_eq!(validate_credential_key("llm.worker-key"), Ok(()));
    assert_eq!(validate_credential_key("tenant_42/session-token"), Ok(()));
}

#[test]
fn rejects_empty_oversized_or_unsafe_credential_keys() {
    assert_eq!(
        validate_credential_key(""),
        Err(CredentialError::InvalidKey)
    );
    assert_eq!(
        validate_credential_key(&"a".repeat(129)),
        Err(CredentialError::InvalidKey)
    );
    assert_eq!(
        validate_credential_key("../../login.key"),
        Err(CredentialError::InvalidKey)
    );
    assert_eq!(
        validate_credential_key("key with spaces"),
        Err(CredentialError::InvalidKey)
    );
}
