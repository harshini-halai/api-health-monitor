const form = document.getElementById("monitor-form");
const urlInput = document.getElementById("url-input");
const resultDiv = document.getElementById("result");
const checkButton = document.getElementById("check-button");

form.addEventListener("submit", async function (event) {
  event.preventDefault();

  const url = urlInput.value.trim();

  resultDiv.classList.remove("hidden");
  resultDiv.textContent = "Checking website...";
  checkButton.disabled = true;

  try {
    const response = await fetch("/monitor", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        url: url,
      }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "Something went wrong");
    }

    resultDiv.innerHTML = `
            <h3>Monitoring Result</h3>
            <p><strong>Website:</strong> ${data.url}</p>
            <p><strong>Status Code:</strong> ${data.status_code}</p>
            <p><strong>Response Time:</strong> ${data.response_time_ms} ms</p>
            <p><strong>Availability:</strong> ${data.is_available ? "Available" : "Unavailable"}</p>
            <p><strong>Message:</strong> ${data.message}</p>
        `;
  } catch (error) {
    resultDiv.textContent = "Error: " + error.message;
  } finally {
    checkButton.disabled = false;
  }
});
