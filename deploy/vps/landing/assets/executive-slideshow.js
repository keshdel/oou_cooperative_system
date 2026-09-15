(function(){
  const gallery = document.getElementById('executiveSlideshow');
  if(!gallery) return;
  const photos = Array.from(gallery.querySelectorAll('.executive-photos img'));
  if(photos.length < 2) return;
  const controls = gallery.querySelector('.photo-controls');
  const toggle = gallery.querySelector('.photo-toggle');
  const count = gallery.querySelector('.photo-count');
  const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  let current = 0;
  let paused = preference.matches;
  let visible = !window.IntersectionObserver;
  let hovered = false;
  let timer;
  let revision = 0;

  function stop(){
    window.clearTimeout(timer);
    revision++;
  }
  function schedule(){
    stop();
    const label = paused ? 'Play slideshow' : 'Pause slideshow';
    toggle.setAttribute('aria-label', label);
    toggle.title = preference.matches ? 'Automatic slideshow disabled by reduced-motion preference' : label;
    toggle.disabled = preference.matches;
    toggle.firstElementChild.textContent = paused ? '\u25b6' : '\u275a\u275a';
    if(!paused && !preference.matches && visible && !hovered && !document.hidden){
      timer = window.setTimeout(function(){ show(current + 1); }, 6000);
    }
  }
  async function show(index){
    stop();
    const request = revision;
    const next = (index + photos.length) % photos.length;
    try{
      // Keep the current photo visible until its replacement can be displayed.
      await photos[next].decode();
      if(request !== revision) return;
      photos.forEach(function(photo, i){
        photo.classList.toggle('is-current', i === next);
        photo.setAttribute('aria-hidden', String(i !== next));
      });
      current = next;
      count.textContent = (current + 1) + ' / ' + photos.length;
    }catch(error){
      // A failed image download must not leave an empty slideshow.
    }
    if(request === revision) schedule();
  }
  controls.hidden = false;
  gallery.querySelector('.previous').addEventListener('click', function(){ paused = true; show(current - 1); });
  gallery.querySelector('.next').addEventListener('click', function(){ paused = true; show(current + 1); });
  toggle.addEventListener('click', function(){ paused = !paused; schedule(); });
  gallery.addEventListener('mouseenter', function(){ hovered = true; schedule(); });
  gallery.addEventListener('mouseleave', function(){ hovered = false; schedule(); });
  gallery.addEventListener('focusin', function(){ paused = true; schedule(); });
  document.addEventListener('visibilitychange', schedule);
  preference.addEventListener('change', function(){ paused = true; schedule(); });
  if(window.IntersectionObserver){
    const observer = new IntersectionObserver(function(entries){
      visible = entries[0].isIntersecting;
      schedule();
    }, {threshold:0});
    observer.observe(gallery);
  }
  schedule();
})();
