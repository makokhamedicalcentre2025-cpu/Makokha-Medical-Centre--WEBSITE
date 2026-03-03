// Global JavaScript Functions

// Form Validation
function validateEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

function validatePhone(phone) {
    const re = /^\+?[\d\s\-\(\)]+$/;
    return re.test(phone);
}

// Debounce Function
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Throttle Function
function throttle(func, limit) {
    let inThrottle;
    return function() {
        const args = arguments;
        const context = this;
        if (!inThrottle) {
            func.apply(context, args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}

// Local Storage Helper
const Storage = {
    set: (key, value) => {
        try {
            localStorage.setItem(key, JSON.stringify(value));
        } catch (e) {
            console.error('LocalStorage error:', e);
        }
    },
    
    get: (key) => {
        try {
            const item = localStorage.getItem(key);
            return item ? JSON.parse(item) : null;
        } catch (e) {
            console.error('LocalStorage error:', e);
            return null;
        }
    },
    
    remove: (key) => {
        try {
            localStorage.removeItem(key);
        } catch (e) {
            console.error('LocalStorage error:', e);
        }
    }
};

// Cookie Helper
const Cookies = {
    set: (name, value, days) => {
        let expires = "";
        if (days) {
            const date = new Date();
            date.setTime(date.getTime() + (days * 24 * 60 * 60 * 1000));
            expires = "; expires=" + date.toUTCString();
        }
        document.cookie = name + "=" + (value || "") + expires + "; path=/";
    },
    
    get: (name) => {
        const nameEQ = name + "=";
        const ca = document.cookie.split(';');
        for(let i = 0; i < ca.length; i++) {
            let c = ca[i];
            while (c.charAt(0) === ' ') c = c.substring(1, c.length);
            if (c.indexOf(nameEQ) === 0) return c.substring(nameEQ.length, c.length);
        }
        return null;
    },
    
    remove: (name) => {
        document.cookie = name + '=; expires=Thu, 01 Jan 1970 00:00:01 GMT; path=/;';
    }
};

// Device Detection
const Device = {
    isMobile: () => window.innerWidth <= 768,
    isTablet: () => window.innerWidth > 768 && window.innerWidth <= 992,
    isDesktop: () => window.innerWidth > 992,
    isTouch: () => 'ontouchstart' in window || navigator.maxTouchPoints > 0
};

// Performance Monitoring
const Performance = {
    startTime: performance.now(),
    
    logPageLoad: () => {
        const loadTime = performance.now() - Performance.startTime;
        console.log(`Page loaded in ${loadTime.toFixed(2)}ms`);
    },
    
    logFirstPaint: () => {
        if ('PerformancePaintTiming' in performance) {
            performance.getEntriesByType('paint').forEach(entry => {
                console.log(`${entry.name}: ${entry.startTime.toFixed(2)}ms`);
            });
        }
    }
};

// Error Tracking
window.addEventListener('error', function(e) {
    console.error('Global error:', e.error);
});

// Initialize on DOM Content Loaded
document.addEventListener('DOMContentLoaded', () => {
    // Add current year to footer
    const yearElement = document.querySelector('#current-year');
    if (yearElement) {
        yearElement.textContent = new Date().getFullYear();
    }
    
    // Performance logging
    Performance.logFirstPaint();
    
    // Add loading class to body
    document.body.classList.remove('loading');
    
    // Initialize tooltips
    const tooltips = document.querySelectorAll('[data-tooltip]');
    tooltips.forEach(tooltip => {
        tooltip.addEventListener('mouseenter', showTooltip);
        tooltip.addEventListener('mouseleave', hideTooltip);
    });
    
    // Handle offline/online status
    window.addEventListener('online', updateOnlineStatus);
    window.addEventListener('offline', updateOnlineStatus);
    
    function updateOnlineStatus() {
        const status = navigator.onLine ? 'online' : 'offline';
        document.body.classList.toggle('offline', !navigator.onLine);
        
        if (!navigator.onLine) {
            showOfflineNotification();
        } else {
            hideOfflineNotification();
        }
    }
    
    function showOfflineNotification() {
        let notification = document.querySelector('.offline-notification');
        if (!notification) {
            notification = document.createElement('div');
            notification.className = 'offline-notification';
            notification.innerHTML = `
                <div class="offline-content">
                    <i class="fas fa-wifi"></i>
                    <span>You are currently offline. Some features may not be available.</span>
                </div>
            `;
            document.body.appendChild(notification);
        }
    }
    
    function hideOfflineNotification() {
        const notification = document.querySelector('.offline-notification');
        if (notification) {
            notification.remove();
        }
    }
    
    // Initialize intersection observers for lazy loading
    const lazyLoadObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const img = entry.target;
                if (img.dataset.src) {
                    img.src = img.dataset.src;
                    img.removeAttribute('data-src');
                }
                lazyLoadObserver.unobserve(img);
            }
        });
    }, { rootMargin: '50px' });
    
    // Observe all lazy images
    document.querySelectorAll('img[data-src]').forEach(img => {
        lazyLoadObserver.observe(img);
    });
    
    // Create back to top button
    createBackToTopButton();
});

// Tooltip functions
function showTooltip(e) {
    const tooltip = e.target;
    const text = tooltip.getAttribute('data-tooltip');
    
    const tooltipEl = document.createElement('div');
    tooltipEl.className = 'tooltip';
    tooltipEl.textContent = text;
    
    document.body.appendChild(tooltipEl);
    
    const rect = tooltip.getBoundingClientRect();
    tooltipEl.style.position = 'fixed';
    tooltipEl.style.left = rect.left + rect.width / 2 + 'px';
    tooltipEl.style.top = rect.top - tooltipEl.offsetHeight - 10 + 'px';
    tooltipEl.style.transform = 'translateX(-50%)';
}

function hideTooltip() {
    const tooltip = document.querySelector('.tooltip');
    if (tooltip) {
        tooltip.remove();
    }
}

// Create Back to Top button
function createBackToTopButton() {
    const button = document.createElement('button');
    button.className = 'back-to-top';
    button.innerHTML = '<i class="fas fa-chevron-up"></i>';
    button.setAttribute('aria-label', 'Back to top');
    document.body.appendChild(button);
    
    // Show/hide button on scroll
    window.addEventListener('scroll', throttle(() => {
        if (window.scrollY > 300) {
            button.classList.add('visible');
        } else {
            button.classList.remove('visible');
        }
    }, 100));
    
    // Scroll to top on click
    button.addEventListener('click', () => {
        window.scrollTo({
            top: 0,
            behavior: 'smooth'
        });
    });
}

// Window load handler
window.addEventListener('load', () => {
    Performance.logPageLoad();
    
    // Remove preloader if exists
    const preloader = document.querySelector('.preloader');
    if (preloader) {
        preloader.style.opacity = '0';
        setTimeout(() => {
            preloader.style.display = 'none';
        }, 300);
    }
});

// Resize handler with debounce
window.addEventListener('resize', debounce(() => {
    // Update any layout-dependent elements
    const isMobile = Device.isMobile();
    document.body.classList.toggle('is-mobile', isMobile);
    document.body.classList.toggle('is-desktop', !isMobile);
}, 250));