// Adds a "Copy" button to every code block in the guide.
(function () {
	function addButtons() {
		var blocks = document.querySelectorAll('.guide-content pre');
		blocks.forEach(function (pre) {
			// Skip algorithm pseudocode blocks (not meant to be copied as code).
			if (pre.closest('.guide-algorithm')) return;
			if (pre.querySelector('.guide-copy-btn')) return;

			var btn = document.createElement('button');
			btn.type = 'button';
			btn.className = 'guide-copy-btn';
			btn.textContent = 'Copy';

			btn.addEventListener('click', function () {
				var code = pre.querySelector('code') || pre;
				var text = code.innerText.replace(/\n$/, '');
				navigator.clipboard.writeText(text).then(function () {
					btn.textContent = 'Copied!';
					btn.classList.add('copied');
					setTimeout(function () {
						btn.textContent = 'Copy';
						btn.classList.remove('copied');
					}, 1500);
				});
			});

			pre.appendChild(btn);
		});
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', addButtons);
	} else {
		addButtons();
	}
})();
