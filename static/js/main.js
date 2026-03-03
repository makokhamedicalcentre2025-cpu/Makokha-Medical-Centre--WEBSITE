/**
 * Makokha Medical Centre - Main JavaScript
 * Handle global functionality and interactions
 */

// ================================
// INITIALIZATION
// ================================

document.addEventListener('DOMContentLoaded', function() {
    initNavDropdowns();
    initMobileMenu();
    initAlerts();
    initScrollBehavior();
    initFormValidation();
});

// ================================
// MOBILE MENU FUNCTIONALITY
// ================================

function initMobileMenu() {
    const toggle = document.getElementById('mobileMenuToggle');
    const menu = document.getElementById('navMenu');
    
    if (!toggle || !menu) return;
    
    toggle.addEventListener('click', function() {
        toggle.classList.toggle('active');
        menu.classList.toggle('active');
    });
    
    // Close menu when clicking on a link
    const links = menu.querySelectorAll('.nav-link, .nav-dropdown-item');
    links.forEach(link => {
        link.addEventListener('click', function(event) {
            if (event.defaultPrevented) return;
            toggle.classList.remove('active');
            menu.classList.remove('active');
        });
    });
    
    // Close menu when clicking outside
    document.addEventListener('click', function(event) {
        if (!event.target.closest('.mobile-menu-toggle') && 
            !event.target.closest('.nav-menu')) {
            toggle.classList.remove('active');
            menu.classList.remove('active');
        }
    });
}

function initNavDropdowns() {
    const dropdowns = document.querySelectorAll('.nav-dropdown');
    if (!dropdowns.length) return;

    const isMobileViewport = () => window.matchMedia('(max-width: 768px)').matches;
    const closeAllDropdowns = () => {
        dropdowns.forEach((dropdown) => dropdown.classList.remove('open'));
    };

    dropdowns.forEach((dropdown) => {
        const toggleLink = dropdown.querySelector('.nav-dropdown-toggle');
        if (!toggleLink) return;

        toggleLink.addEventListener('click', (event) => {
            if (!isMobileViewport()) return;
            if (dropdown.classList.contains('open')) return;

            event.preventDefault();
            closeAllDropdowns();
            dropdown.classList.add('open');
        });
    });

    document.addEventListener('click', (event) => {
        if (event.target.closest('.nav-dropdown')) return;
        closeAllDropdowns();
    });

    window.addEventListener('resize', () => {
        if (!isMobileViewport()) {
            closeAllDropdowns();
        }
    });
}

// ================================
// ALERT DISMISSAL
// ================================

function initAlerts() {
    const closeButtons = document.querySelectorAll('.alert-close');
    
    closeButtons.forEach(button => {
        button.addEventListener('click', function() {
            const alert = this.closest('.alert');
            if (alert) {
                alert.style.animation = 'fadeOut 0.3s ease';
                setTimeout(() => {
                    alert.remove();
                }, 300);
            }
        });
    });
    
    // Auto-dismiss alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            if (alert && alert.parentNode) {
                alert.style.animation = 'fadeOut 0.3s ease';
                setTimeout(() => {
                    alert.remove();
                }, 300);
            }
        }, 5000);
    });
}

// ================================
// SCROLL BEHAVIOR
// ================================

function initScrollBehavior() {
    const header = document.getElementById('header');
    const heroSection = document.querySelector('.hero');
    
    if (!header) return;

    const updateHeaderState = () => {
        if (window.scrollY > 50) {
            header.classList.add('scrolled');
        } else {
            header.classList.remove('scrolled');
        }

        if (heroSection) {
            const heroRect = heroSection.getBoundingClientRect();
            const headerHeight = header.offsetHeight || 70;
            const isOverHero = heroRect.bottom > headerHeight;
            header.classList.toggle('over-hero', isOverHero);
        } else {
            header.classList.remove('over-hero');
        }
    };

    updateHeaderState();
    window.addEventListener('scroll', updateHeaderState, { passive: true });
    window.addEventListener('resize', updateHeaderState);
}

// ================================
// FORM VALIDATION
// ================================

function initFormValidation() {
    const forms = document.querySelectorAll('form[data-validate]');
    
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            if (!validateForm(this)) {
                e.preventDefault();
            }
        });
    });
}

function validateForm(form) {
    let isValid = true;
    const inputs = form.querySelectorAll('[required]');
    
    inputs.forEach(input => {
        if (!validateInput(input)) {
            isValid = false;
        }
    });
    
    return isValid;
}

function validateInput(input) {
    const value = input.value.trim();
    let isValid = true;
    
    // Check if required field is empty
    if (input.required && !value) {
        isValid = false;
    }
    
    // Check email format
    if (input.type === 'email' && value) {
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        isValid = emailRegex.test(value);
    }
    
    // Check phone format
    if (input.type === 'tel' && value) {
        const phoneRegex = /^\+?[\d\s\-\(\)]+$/;
        isValid = phoneRegex.test(value);
    }
    
    // Update UI
    if (isValid) {
        input.classList.remove('form-error');
        const errorMsg = input.nextElementSibling;
        if (errorMsg && errorMsg.classList.contains('form-error-message')) {
            errorMsg.remove();
        }
    } else {
        input.classList.add('form-error');
        if (!input.nextElementSibling || !input.nextElementSibling.classList.contains('form-error-message')) {
            const errorMsg = document.createElement('div');
            errorMsg.className = 'form-error-message';
            errorMsg.textContent = `Please enter a valid ${input.type || 'field'}`;
            input.parentNode.insertBefore(errorMsg, input.nextSibling);
        }
    }
    
    return isValid;
}

// ================================
// UTILITY FUNCTIONS
// ================================

/**
 * Validate email format
 */
function validateEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

/**
 * Validate phone format
 */
function validatePhone(phone) {
    const re = /^\+?[\d\s\-\(\)]+$/;
    return re.test(phone);
}

/**
 * Debounce function for performance
 */
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

/**
 * Throttle function for performance
 */
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

/**
 * Local Storage Helper
 */
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

/**
 * Fetch API helper with error handling
 */
async function fetchApi(url, options = {}) {
    try {
        const response = await fetch(url, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('Fetch error:', error);
        throw error;
    }
}

/**
 * Show notification
 */
function showNotification(message, type = 'info', duration = 5000) {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    alertDiv.innerHTML = `
        <div class="container">
            <button type="button" class="alert-close" data-dismiss="alert">&times;</button>
            ${message}
        </div>
    `;
    
    const mainContent = document.querySelector('.main-content');
    if (mainContent) {
        mainContent.insertBefore(alertDiv, mainContent.firstChild);
    } else {
        document.body.insertBefore(alertDiv, document.body.firstChild);
    }
    
    const closeBtn = alertDiv.querySelector('.alert-close');
    closeBtn.addEventListener('click', () => {
        alertDiv.remove();
    });
    
    if (duration) {
        setTimeout(() => {
            if (alertDiv && alertDiv.parentNode) {
                alertDiv.remove();
            }
        }, duration);
    }
}

/**
 * Format date
 */
function formatDate(date, format = 'short') {
    const d = new Date(date);
    
    if (format === 'short') {
        return d.toLocaleDateString('en-US', { 
            year: 'numeric', 
            month: 'short', 
            day: 'numeric' 
        });
    } else if (format === 'long') {
        return d.toLocaleDateString('en-US', { 
            weekday: 'long',
            year: 'numeric', 
            month: 'long', 
            day: 'numeric' 
        });
    } else if (format === 'time') {
        return d.toLocaleTimeString('en-US', { 
            hour: '2-digit', 
            minute: '2-digit' 
        });
    }
    
    return d.toLocaleDateString('en-US');
}

/**
 * Scroll to element
 */
function scrollToElement(selector) {
    const element = document.querySelector(selector);
    if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

/**
 * Get query parameter from URL
 */
function getQueryParam(name) {
    const url = new URLSearchParams(window.location.search);
    return url.get(name);
}

/**
 * Add CSS animation
 */
function addCSSAnimation(element, animationName, duration = 0.5) {
    const style = document.createElement('style');
    style.textContent = `
        @keyframes ${animationName} {
            0% { opacity: 1; }
            100% { opacity: 0; }
        }
        .animate-${animationName} {
            animation: ${animationName} ${duration}s ease forwards;
        }
    `;
    document.head.appendChild(style);
    element.classList.add(`animate-${animationName}`);
}

/**
 * Emoji support check
 */
function supportsEmoji() {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    ctx.font = '32px Arial';
    ctx.fillText('😀', 0, 32);
    return canvas.toDataURL().indexOf('image/png') === 5;
}

// Add fadeOut animation
const style = document.createElement('style');
style.textContent = `
    @keyframes fadeOut {
        from {
            opacity: 1;
            transform: translateY(0);
        }
        to {
            opacity: 0;
            transform: translateY(-20px);
        }
    }
`;
document.head.appendChild(style);
