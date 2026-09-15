const $ = (selector) => document.querySelector(selector);

let lastReferenceId = null;

function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("show");

    setTimeout(() => {
        toast.classList.remove("show");
    }, 3000);
}

function addLog(message) {
    const log = $("#activityLog");
    const entry = document.createElement("div");
    entry.className = "log-entry";

    const text = document.createElement("span");
    text.textContent = message;

    const time = document.createElement("time");
    time.textContent = new Date().toLocaleTimeString();

    entry.append(text, time);
    log.prepend(entry);
}

function setStatus(element, text, type = "waiting") {
    element.textContent = text;
    element.className = `status-box ${type}`;
}

function algorithm() {
    return $("#algorithm").value;
}

async function api(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({
        success: false,
        error: "Invalid server response.",
    }));

    if (!response.ok || data.success === false) {
        throw new Error(data.error || "Server error.");
    }

    return data;
}

function postJson(url, body = {}) {
    return api(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
    });
}

function formatBytes(bytes) {
    if (bytes === 0) return "0 B";

    const units = ["B", "KB", "MB", "GB"];
    const index = Math.floor(Math.log(bytes) / Math.log(1024));

    return `${(bytes / Math.pow(1024, index)).toFixed(index ? 2 : 0)} ${units[index]}`;
}

function updateImage(role, image) {
    const prefix = role === "original" ? "original" : "received";

    $(`#${prefix}Filename`).textContent = image.filename;
    $(`#${prefix}Size`).textContent = formatBytes(image.size);
    $(`#${prefix}Crc`).textContent = image.crc;

    const preview = $(`#${prefix}Preview`);
    const empty = $(`#${prefix}Empty`);

    preview.src = `${image.preview_url}?t=${Date.now()}`;
    preview.classList.remove("hidden");
    empty.classList.add("hidden");

    const sideImage = role === "original"
        ? $("#sideOriginal")
        : $("#sideReceived");

    sideImage.src = `${image.preview_url}?t=${Date.now()}`;
    sideImage.classList.remove("hidden");

    $(`#${prefix}Summary`).textContent = image.filename;
    $("#algorithmSummary").textContent = image.algorithm;
}

function resetStatusCards() {
    setStatus($("#crcStatus"), "CRC STATUS: WAITING");
    setStatus($("#byteStatus"), "BYTE-BY-BYTE STATUS: WAITING");
    setStatus($("#verifyCrcStatus"), "CRC STATUS: WAITING");
    setStatus($("#verifyByteStatus"), "BYTE-BY-BYTE STATUS: WAITING");
    setStatus($("#corruptionStatus"), "CORRUPTION STATUS: WAITING");
    setStatus($("#overallStatus"), "OVERALL STATUS: WAITING");
    setStatus($("#referenceStatus"), "REFERENCE: WAITING");
}

async function upload(role, file) {
    if (!file) {
        throw new Error("No image selected.");
    }

    const formData = new FormData();
    formData.append("image", file);
    formData.append("algorithm", algorithm());

    const data = await api(`/api/upload/${role}`, {
        method: "POST",
        body: formData,
    });

    updateImage(role, data.image);
    addLog(data.event);
    showToast(`${role} image selected`);
}

$("#originalInput").addEventListener("change", async (event) => {
    try {
        await upload("original", event.target.files[0]);
    } catch (error) {
        showToast(error.message);
    }
});

$("#receivedInput").addEventListener("change", async (event) => {
    try {
        await upload("received", event.target.files[0]);
    } catch (error) {
        showToast(error.message);
    }
});

$("#algorithm").addEventListener("change", () => {
    $("#algorithmSummary").textContent = algorithm();
});

$("#calculateBtn").addEventListener("click", async () => {
    try {
        const data = await postJson("/api/calculate-crc", {
            algorithm: algorithm(),
        });

        for (const [role, image] of Object.entries(data.images)) {
            updateImage(role, image);
        }

        addLog("CRC calculated");
        showToast("CRC calculated");
    } catch (error) {
        showToast(error.message);
    }
});

$("#verifyCrcBtn").addEventListener("click", async () => {
    try {
        const data = await postJson("/api/verify-crc", {
            algorithm: algorithm(),
        });

        const type = data.match ? "success" : "error";

        setStatus(
            $("#crcStatus"),
            `CRC STATUS: ${data.match ? "MATCH" : "MISMATCH"} — ` +
            `${data.original_crc} / ${data.received_crc}`,
            type
        );

        addLog(data.event);
    } catch (error) {
        showToast(error.message);
    }
});

$("#verifyBytesBtn").addEventListener("click", async () => {
    try {
        const data = await postJson("/api/verify-bytes");

        $("#byteOriginalSize").textContent = formatBytes(data.original_size);
        $("#byteReceivedSize").textContent = formatBytes(data.received_size);
        $("#matchingBytes").textContent = data.matching_bytes;
        $("#differentBytes").textContent = data.different_bytes;
        $("#firstMismatch").textContent =
            data.first_mismatch_position === null
                ? "None"
                : data.first_mismatch_position;
        $("#similarity").textContent = `${data.similarity_percentage}%`;

        setStatus(
            $("#byteStatus"),
            `BYTE-BY-BYTE STATUS: ${
                data.identical
                    ? "BYTE-FOR-BYTE IDENTICAL"
                    : "DIFFERENT"
            }`,
            data.identical ? "success" : "error"
        );

        addLog(data.event);
    } catch (error) {
        showToast(error.message);
    }
});

$("#transferBtn").addEventListener("click", async () => {
    try {
        const data = await postJson("/api/transfer", {
            algorithm: algorithm(),
        });

        updateImage("received", data.image);
        $("#corruptionInfo").classList.add("hidden");

        addLog(data.event);
        showToast("Exact binary transfer simulated");
    } catch (error) {
        showToast(error.message);
    }
});

$("#corruptBtn").addEventListener("click", async () => {
    try {
        const data = await postJson("/api/corrupt");

        const info = $("#corruptionInfo");
        info.classList.remove("hidden");
        info.textContent =
            `Changed byte position ${data.changed_byte_position}: ` +
            `${data.original_byte} → ${data.changed_byte}`;

        addLog(data.event);
        showToast("Received image corrupted");
    } catch (error) {
        showToast(error.message);
    }
});

$("#verifyBtn").addEventListener("click", async () => {
    try {
        const data = await postJson("/api/verify", {
            algorithm: algorithm(),
        });

        setStatus(
            $("#verifyCrcStatus"),
            `CRC STATUS: ${data.crc_status}`,
            data.crc.match ? "success" : "error"
        );

        setStatus(
            $("#verifyByteStatus"),
            `BYTE-BY-BYTE STATUS: ${data.byte_status}`,
            data.bytes.identical ? "success" : "error"
        );

        setStatus(
            $("#corruptionStatus"),
            `CORRUPTION STATUS: ${data.corruption_status}`,
            data.intact ? "success" : "error"
        );

        setStatus(
            $("#overallStatus"),
            data.overall_status,
            data.intact ? "success" : "error"
        );

        $("#overallSummary").textContent = data.intact ? "INTACT" : "CORRUPTED";
        $("#overallSummary").style.color = data.intact
            ? "var(--green)"
            : "var(--red)";

        addLog(data.event);
        showToast(data.overall_status);
    } catch (error) {
        showToast(error.message);
    }
});

$("#saveReferenceBtn").addEventListener("click", async () => {
    try {
        const data = await postJson("/api/save-reference", {
            algorithm: algorithm(),
        });

        lastReferenceId = data.reference.reference_id;

        setStatus(
            $("#referenceStatus"),
            `REFERENCE SAVED: ${data.reference.crc}`,
            "success"
        );

        addLog(data.event);
        showToast("Reference saved");
    } catch (error) {
        showToast(error.message);
    }
});

$("#checkReferenceBtn").addEventListener("click", async () => {
    try {
        const data = await postJson("/api/check-reference", {
            algorithm: algorithm(),
            reference_id: lastReferenceId,
        });

        setStatus(
            $("#referenceStatus"),
            `REFERENCE: ${data.match ? "MATCH" : "MISMATCH"} — ${data.current_crc}`,
            data.match ? "success" : "error"
        );

        addLog(data.event);
    } catch (error) {
        showToast(error.message);
    }
});

$("#resetBtn").addEventListener("click", async () => {
    try {
        await postJson("/api/reset");

        $("#originalInput").value = "";
        $("#receivedInput").value = "";

        for (const role of ["original", "received"]) {
            const prefix = role === "original" ? "original" : "received";

            $(`#${prefix}Filename`).textContent = "—";
            $(`#${prefix}Size`).textContent = "—";
            $(`#${prefix}Crc`).textContent = "—";
            $(`#${prefix}Summary`).textContent = "Waiting";
            $(`#${prefix}Preview`).src = "";
            $(`#${prefix}Preview`).classList.add("hidden");
            $(`#${prefix}Empty`).classList.remove("hidden");
        }

        $("#sideOriginal").src = "";
        $("#sideReceived").src = "";
        $("#sideOriginal").classList.add("hidden");
        $("#sideReceived").classList.add("hidden");

        $("#algorithm").value = "CRC-32";
        $("#algorithmSummary").textContent = "CRC-32";
        $("#overallSummary").textContent = "Waiting";
        $("#overallSummary").style.color = "";

        $("#corruptionInfo").classList.add("hidden");

        for (const id of [
            "byteOriginalSize",
            "byteReceivedSize",
            "matchingBytes",
            "differentBytes",
            "firstMismatch",
            "similarity",
        ]) {
            $(`#${id}`).textContent = "—";
        }

        resetStatusCards();

        $("#activityLog").innerHTML =
            '<div class="log-entry"><span>System reset</span><time>now</time></div>';

        lastReferenceId = null;
        showToast("Dashboard reset");
    } catch (error) {
        showToast(error.message);
    }
});