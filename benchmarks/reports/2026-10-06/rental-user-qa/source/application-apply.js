(function applicationPage() {
  const inviteContext = document.getElementById("invite-context");
  const applicationStatus = document.getElementById("application-status");
  const applicationMeta = document.getElementById("application-meta");
  const applicantNotesInput = document.getElementById("applicant-notes");
  const preferredMoveInDateInput = document.getElementById("preferred-move-in-date");
  const consentToScreeningInput = document.getElementById("consent-to-screening");
  const hasCoApplicantInput = document.getElementById("has-co-applicant");
  const coApplicantSection = document.getElementById("co-applicant-section");
  const applicationForm = document.getElementById("application-form");
  const statusBanner = document.getElementById("status-banner");
  let toastTimeoutId = null;
  let toastElement = null;
  const toastDurations = {
    success: 3200,
    info: 4200,
    error: 5600,
  };

  const token = new URLSearchParams(window.location.search).get("token");
  let currentApplication = null;

  const fieldIds = [
    "primary-first-name",
    "primary-last-name",
    "primary-email",
    "primary-phone",
    "primary-employer-name",
    "primary-job-title",
    "primary-monthly-income",
    "primary-employment-start-date",
    "primary-current-address1",
    "primary-current-city",
    "primary-current-state",
    "primary-current-postal-code",
    "primary-current-landlord-name",
    "primary-current-landlord-phone",
    "co-first-name",
    "co-last-name",
    "co-email",
    "co-phone",
    "co-employer-name",
    "co-job-title",
    "co-monthly-income",
  ];

  const field = (id) => document.getElementById(id);

  function normalizeToastVariant(variant) {
    return ["success", "info", "error"].includes(variant) ? variant : "success";
  }

  function ensureToast() {
    if (toastElement && document.body.contains(toastElement)) {
      return toastElement;
    }

    toastElement = document.createElement("div");
    toastElement.className = "floating-toast hidden";
    toastElement.setAttribute("aria-live", "polite");
    document.body.appendChild(toastElement);
    return toastElement;
  }

  function showToast(message, variant = "success") {
    const element = ensureToast();
    const normalizedVariant = normalizeToastVariant(variant);
    if (toastTimeoutId) {
      window.clearTimeout(toastTimeoutId);
      toastTimeoutId = null;
    }

    element.textContent = message;
    element.className = `floating-toast ${normalizedVariant}`;
    toastTimeoutId = window.setTimeout(() => {
      element.textContent = "";
      element.className = "floating-toast hidden";
      toastTimeoutId = null;
    }, toastDurations[normalizedVariant]);
  }

  function showBanner(message, variant = "success") {
    showToast(message, variant);
    statusBanner.textContent = message;
    statusBanner.className = `status-banner ${variant}`;
  }

  function setStatus(status) {
    applicationStatus.className = `status-pill ${status || ""}`;
    applicationStatus.textContent = (status || "DRAFT").replaceAll("_", " ");
  }

  async function fetchInvite() {
    if (!token) {
      showBanner("Missing application token.", "error");
      return;
    }

    const response = await fetch(`/api/applications/public/${encodeURIComponent(token)}`);
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error || "Unable to load application.");
    }

    const location = [body.invite.property?.name, body.invite.unit?.name].filter(Boolean).join(" · ");
    inviteContext.textContent = location
      ? `Invited for ${location}. ${body.invite.note || ""}`.trim()
      : "Complete the application below.";
    applicationMeta.innerHTML = `
      <div><strong>Invite email:</strong> ${body.invite.email}</div>
      <div><strong>Expires:</strong> ${body.invite.expiresAt ? new Date(body.invite.expiresAt).toLocaleString() : "No expiration"}</div>
    `;

    currentApplication = body.application;
    if (currentApplication) {
      hydrateApplication(currentApplication);
    } else {
      setStatus("INVITED");
      hydrateInviteContact(body.invite);
    }
  }

  function hydrateInviteContact(invite) {
    if (!invite) {
      return;
    }

    if (field("primary-first-name") && !field("primary-first-name").value.trim()) {
      field("primary-first-name").value = invite.firstName || "";
    }
    if (field("primary-last-name") && !field("primary-last-name").value.trim()) {
      field("primary-last-name").value = invite.lastName || "";
    }
    if (field("primary-email") && !field("primary-email").value.trim()) {
      field("primary-email").value = invite.email || "";
    }
    if (field("primary-phone") && !field("primary-phone").value.trim()) {
      field("primary-phone").value = invite.phone || "";
    }
  }

  function hydrateApplicant(prefix, applicant) {
    field(`${prefix}-first-name`).value = applicant.firstName || "";
    field(`${prefix}-last-name`).value = applicant.lastName || "";
    field(`${prefix}-email`).value = applicant.email || "";
    field(`${prefix}-phone`).value = applicant.phone || "";
    if (field(`${prefix}-employer-name`)) field(`${prefix}-employer-name`).value = applicant.employerName || "";
    if (field(`${prefix}-job-title`)) field(`${prefix}-job-title`).value = applicant.jobTitle || "";
    if (field(`${prefix}-monthly-income`)) field(`${prefix}-monthly-income`).value = applicant.monthlyIncome || "";
    if (field(`${prefix}-employment-start-date`) && applicant.employmentStartDate) {
      field(`${prefix}-employment-start-date`).value = applicant.employmentStartDate.slice(0, 10);
    }
    if (field(`${prefix}-current-address1`)) field(`${prefix}-current-address1`).value = applicant.currentAddress1 || "";
    if (field(`${prefix}-current-city`)) field(`${prefix}-current-city`).value = applicant.currentCity || "";
    if (field(`${prefix}-current-state`)) field(`${prefix}-current-state`).value = applicant.currentState || "";
    if (field(`${prefix}-current-postal-code`)) field(`${prefix}-current-postal-code`).value = applicant.currentPostalCode || "";
    if (field(`${prefix}-current-landlord-name`)) field(`${prefix}-current-landlord-name`).value = applicant.currentLandlordName || "";
    if (field(`${prefix}-current-landlord-phone`)) field(`${prefix}-current-landlord-phone`).value = applicant.currentLandlordPhone || "";
  }

  function syncCoApplicantFields() {
    const enabled = hasCoApplicantInput.checked;
    coApplicantSection.classList.toggle("hidden", !enabled);
    coApplicantSection.querySelectorAll("input").forEach((input) => {
      input.disabled = !enabled || hasCoApplicantInput.disabled;
    });
  }

  function hydrateApplication(application) {
    setStatus(application.status);
    applicantNotesInput.value = application.applicantNotes || "";
    preferredMoveInDateInput.value = application.preferredMoveInDate
      ? application.preferredMoveInDate.slice(0, 10)
      : "";
    consentToScreeningInput.checked = Boolean(application.consentToScreeningAt);

    const primary = application.applicants.find((applicant) => applicant.role === "PRIMARY");
    const coApplicant = application.applicants.find((applicant) => applicant.role === "CO_APPLICANT");
    if (primary) {
      hydrateApplicant("primary", primary);
    }
    if (coApplicant) {
      hasCoApplicantInput.checked = true;
      hydrateApplicant("co", coApplicant);
    } else {
      hasCoApplicantInput.checked = false;
    }

    if (!["DRAFT", "INVITED", "IN_PROGRESS"].includes(application.status)) {
      fieldIds.forEach((id) => {
        const input = field(id);
        if (input) input.disabled = true;
      });
      applicantNotesInput.disabled = true;
      preferredMoveInDateInput.disabled = true;
      consentToScreeningInput.disabled = true;
      hasCoApplicantInput.disabled = true;
      applicationForm.querySelector('button[type="submit"]').disabled = true;
    }
    syncCoApplicantFields();
  }

  function buildApplicant(prefix, role) {
    const firstName = field(`${prefix}-first-name`).value.trim();
    const lastName = field(`${prefix}-last-name`).value.trim();
    const email = field(`${prefix}-email`).value.trim();
    const phone = field(`${prefix}-phone`).value.trim();

    if (!firstName || !lastName || !email || !phone) {
      return null;
    }

    return {
      role,
      firstName,
      lastName,
      email,
      phone,
      employerName: field(`${prefix}-employer-name`)?.value || undefined,
      jobTitle: field(`${prefix}-job-title`)?.value || undefined,
      monthlyIncome: field(`${prefix}-monthly-income`)?.value || undefined,
      employmentStartDate: field(`${prefix}-employment-start-date`)?.value || undefined,
      currentAddress1: field(`${prefix}-current-address1`)?.value || undefined,
      currentCity: field(`${prefix}-current-city`)?.value || undefined,
      currentState: field(`${prefix}-current-state`)?.value || undefined,
      currentPostalCode: field(`${prefix}-current-postal-code`)?.value || undefined,
      currentLandlordName: field(`${prefix}-current-landlord-name`)?.value || undefined,
      currentLandlordPhone: field(`${prefix}-current-landlord-phone`)?.value || undefined,
    };
  }

  async function saveApplication(submit) {
    const primaryApplicant = buildApplicant("primary", "PRIMARY");
    if (!primaryApplicant) {
      throw new Error("Primary applicant first name, last name, email, and phone are required.");
    }

    const applicants = [primaryApplicant];
    if (hasCoApplicantInput.checked) {
      const coApplicant = buildApplicant("co", "CO_APPLICANT");
      if (!coApplicant) {
        throw new Error("If you add a co-applicant, include first name, last name, email, and phone.");
      }
      applicants.push(coApplicant);
    }

    const response = await fetch(
      `/api/applications/public/${encodeURIComponent(token)}${submit ? "/submit" : ""}`,
      {
        method: submit ? "POST" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          applicantNotes: applicantNotesInput.value || undefined,
          preferredMoveInDate: preferredMoveInDateInput.value
            ? new Date(preferredMoveInDateInput.value).toISOString()
            : undefined,
          consentToScreening: consentToScreeningInput.checked,
          applicants,
        }),
      }
    );
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error || "Unable to save application.");
    }

    currentApplication = body;
    hydrateApplication(body);
    showBanner(submit ? "Submitted application." : "Saved application.");
  }

  hasCoApplicantInput.addEventListener("change", syncCoApplicantFields);
  syncCoApplicantFields();

  applicationForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveApplication(true).catch((error) => showBanner(error.message, "error"));
  });

  fetchInvite().catch((error) => showBanner(error.message, "error"));
})();
