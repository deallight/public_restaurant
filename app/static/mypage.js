const reviewGrid = document.querySelector("#mypage-review-grid");
const reviewCount = document.querySelector("#mypage-review-count");
const reviewTotal = document.querySelector("#mypage-review-total");
const reviewStatus = document.querySelector("#mypage-review-status");
const reviewEmptyTemplate = document.querySelector("#mypage-review-empty-template");

function setReviewStatus(message, isError = false) {
  if (!reviewStatus) return;
  reviewStatus.textContent = message;
  reviewStatus.classList.toggle("is-error", isError);
}

function updateReviewCount() {
  if (!reviewGrid) return;
  const count = reviewGrid.querySelectorAll("[data-review-card]").length;
  if (reviewCount) reviewCount.textContent = String(count);
  if (reviewTotal) reviewTotal.textContent = `${count}개`;
  if (count === 0 && reviewEmptyTemplate && !reviewGrid.querySelector(".mypage-empty")) {
    reviewGrid.append(reviewEmptyTemplate.content.cloneNode(true));
  }
}

document.querySelectorAll(".mypage-review-delete").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const card = form.closest("[data-review-card]");
    if (!button || !card || button.disabled) return;

    button.disabled = true;
    button.textContent = "삭제 중…";
    setReviewStatus("");
    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: { Accept: "application/json" },
        body: new FormData(form),
        credentials: "same-origin",
      });
      const contentType = response.headers.get("Content-Type") || "";
      const result = contentType.includes("application/json")
        ? await response.json()
        : {};
      if (!response.ok || result.status !== "deleted") {
        throw new Error(result.error || "리뷰를 삭제하지 못했습니다.");
      }
      card.remove();
      updateReviewCount();
      setReviewStatus("리뷰를 삭제했습니다.");
    } catch (error) {
      button.disabled = false;
      button.textContent = "리뷰 삭제";
      setReviewStatus(error.message || "리뷰를 삭제하지 못했습니다.", true);
    }
  });
});
