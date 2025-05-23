function createPageParticles() {
  const particlesContainer = document.getElementById("pageParticles");
  if (!particlesContainer) return;
  particlesContainer.innerHTML = "";
  for (let i = 0; i < 30; i++) {
    const particle = document.createElement("div");
    particle.className = "particle";
    particle.style.left = Math.random() * 100 + "%";
    particle.style.animationDelay = Math.random() * 4 + "s";
    particle.style.animationDuration = (Math.random() * 2 + 3) + "s";
    particle.style.background = `rgba(${Math.random() > 0.5 ? '56, 239, 125' : '17, 153, 142'}, 0.7)`;
    particlesContainer.appendChild(particle);
  }
}

function startPageLoadingProgress() {
  const progressBar = document.getElementById("pageProgressBar");
  const progressText = document.getElementById("pageProgressText");
  const loadingText = document.getElementById("pageLoadingText");
  if (!progressBar || !progressText || !loadingText) return;

  const messages = [
    "正在載入資料...",
    "初始化使用者狀態...",
    "請稍候...",
    "即將完成！"
  ];

  let progress = 0;
  let messageIndex = 0;
  const progressInterval = setInterval(() => {
    progress += Math.random() * 20 + 10;
    if (progress > 100) progress = 100;

    progressBar.style.width = progress + "%";
    progressText.textContent = Math.round(progress) + "%";

    if (messageIndex < messages.length - 1 && progress > (messageIndex + 1) * 25) {
      messageIndex++;
      loadingText.textContent = messages[messageIndex];
    }

    if (progress >= 100) {
      clearInterval(progressInterval);
      loadingText.textContent = messages[messages.length - 1];
    }
  }, 300);

  window.pageLoadingInterval = progressInterval;
}

createPageParticles();
